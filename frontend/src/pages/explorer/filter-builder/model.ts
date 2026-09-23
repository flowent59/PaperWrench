/**
 * Pure helpers for editing a FilterSet in the browser.
 *
 * No React, no fetching - just tree edits, so they can be tested directly.
 *
 * The one rule these encode: **the builder may only produce shapes the
 * backend compiler can translate.** It is not the authority (the backend is,
 * and every filter goes through `/filters/validate` before it runs), but a
 * UI that lets you build something guaranteed to be refused is a bad UI. So:
 *
 * - the root is always AND, because OR over core fields has no server-side
 *   form and OR over a mix of core and custom fields has none either;
 * - a group added inside it is always OR and accepts custom fields only,
 *   which is exactly the one OR shape Paperless can express;
 * - `NOT` is not offered at all in this version.
 *
 * All three of those come from `GET /filters/capabilities`, and
 * `groupingAllows` below checks them rather than assuming them, so the day
 * the compiler grows a capability the UI can start offering it without this
 * file lying in the meantime.
 */

import type {
  FieldCapability,
  FilterCondition,
  FilterGroup,
  FilterNode,
  FilterOperator,
  FilterSet,
  GroupingCapabilities,
  OperatorCapability,
} from '@/api/types'

/** An empty filter: valid, compiles to no parameters, matches everything. */
export function emptyFilterSet(): FilterSet {
  return { root: { kind: 'group', operator: 'and', children: [] } }
}

export function isEmpty(filters: FilterSet): boolean {
  return filters.root.children.length === 0
}

/** The reference key the capabilities use: `core:title`, `custom_field:17`. */
export function fieldKey(condition: FilterCondition): string {
  return condition.field.source === 'core'
    ? `core:${condition.field.name}`
    : `custom_field:${condition.field.field_id}`
}

export function findField(
  fields: FieldCapability[],
  key: string,
): FieldCapability | undefined {
  return fields.find((field) => field.key === key)
}

export function findOperator(
  field: FieldCapability | undefined,
  operator: FilterOperator,
): OperatorCapability | undefined {
  return field?.operators.find((candidate) => candidate.operator === operator)
}

/**
 * A blank condition on `field`, using its first (most common) operator.
 *
 * The value starts as `null` rather than `''`: an empty string is a value the
 * backend refuses on purpose (Paperless would drop the filter entirely and
 * return every document), so a fresh row must not look like it already has
 * one.
 */
export function conditionFor(field: FieldCapability): FilterCondition {
  const operator = field.operators[0]
  const base =
    field.source === 'core'
      ? ({ source: 'core', name: field.key.slice('core:'.length) } as const)
      : ({
          source: 'custom_field',
          field_id: field.custom_field_id as number,
          display_name: field.label,
        } as const)
  return {
    kind: 'condition',
    field: base,
    operator: operator?.operator ?? 'equals',
    value: defaultValueFor(field, operator),
  }
}

/** The value a freshly-picked (field, operator) pair should start with. */
export function defaultValueFor(
  field: FieldCapability,
  operator: OperatorCapability | undefined,
): unknown {
  if (operator === undefined || operator.value_shape === 'none') return null
  if (operator.multi) return []
  if (operator.value_shape === 'boolean') return true
  if (operator.value_shape === 'select_option') return field.select_options[0]?.id ?? null
  // Text, decimal, date, numbers: left null until the user types something,
  // so an untouched row reports "a value is required" rather than silently
  // meaning something.
  return null
}

/** Whether the grouping capabilities allow offering an OR group at all. */
export function groupingAllows(grouping: GroupingCapabilities | undefined): {
  orGroups: boolean
  onlyCustomFieldsInOr: boolean
} {
  if (grouping === undefined) return { orGroups: false, onlyCustomFieldsInOr: true }
  return {
    orGroups: grouping.or_custom_fields_supported,
    // If the backend ever learns to compile a mixed OR, the builder stops
    // restricting the field list inside a group - without a change here.
    onlyCustomFieldsInOr: !grouping.or_mixed_supported || !grouping.or_core_fields_supported,
  }
}

/**
 * The fields that may be picked at a given place in the tree.
 *
 * Inside an OR group that is only a legal shape for custom fields, only
 * custom fields are offered. This is the "the user cannot build a form we
 * know will be refused" requirement, expressed as a filtered dropdown rather
 * than as an error after the fact.
 */
export function selectableFields(
  fields: FieldCapability[],
  insideOrGroup: boolean,
  grouping: GroupingCapabilities | undefined,
): FieldCapability[] {
  if (!insideOrGroup) return fields
  const { onlyCustomFieldsInOr } = groupingAllows(grouping)
  return onlyCustomFieldsInOr
    ? fields.filter((field) => field.source === 'custom_field')
    : fields
}

// ---------------------------------------------------------------- tree edits
//
// Every edit returns a new FilterSet. Nothing is mutated in place, so React
// state updates are straightforward and a filter can be compared to its
// previous version by identity.

function replaceChild(group: FilterGroup, index: number, child: FilterNode): FilterGroup {
  const children = group.children.slice()
  children[index] = child
  return { ...group, children }
}

export function addCondition(filters: FilterSet, condition: FilterCondition): FilterSet {
  return { root: { ...filters.root, children: [...filters.root.children, condition] } }
}

export function addOrGroup(filters: FilterSet, first: FilterCondition): FilterSet {
  const group: FilterGroup = { kind: 'group', operator: 'or', children: [first] }
  return { root: { ...filters.root, children: [...filters.root.children, group] } }
}

export function removeAt(filters: FilterSet, index: number): FilterSet {
  const children = filters.root.children.filter((_, position) => position !== index)
  return { root: { ...filters.root, children } }
}

export function updateAt(filters: FilterSet, index: number, node: FilterNode): FilterSet {
  return { root: replaceChild(filters.root, index, node) }
}

export function addToGroup(
  filters: FilterSet,
  groupIndex: number,
  condition: FilterCondition,
): FilterSet {
  const group = filters.root.children[groupIndex]
  if (group === undefined || group.kind !== 'group') return filters
  return updateAt(filters, groupIndex, {
    ...group,
    children: [...group.children, condition],
  })
}

/**
 * Remove one branch of an OR group, collapsing the group when it empties.
 *
 * A group with no children is a validation error on the backend (it is
 * neither "match everything" nor "match nothing"), and a group with exactly
 * one child means the same thing as that child - so removing the second-last
 * branch leaves a plain condition rather than a pointless one-branch OR.
 */
export function removeFromGroup(
  filters: FilterSet,
  groupIndex: number,
  childIndex: number,
): FilterSet {
  const group = filters.root.children[groupIndex]
  if (group === undefined || group.kind !== 'group') return filters
  const remaining = group.children.filter((_, position) => position !== childIndex)
  if (remaining.length === 0) return removeAt(filters, groupIndex)
  if (remaining.length === 1 && remaining[0] !== undefined) {
    return updateAt(filters, groupIndex, remaining[0])
  }
  return updateAt(filters, groupIndex, { ...group, children: remaining })
}

export function updateInGroup(
  filters: FilterSet,
  groupIndex: number,
  childIndex: number,
  child: FilterNode,
): FilterSet {
  const group = filters.root.children[groupIndex]
  if (group === undefined || group.kind !== 'group') return filters
  return updateAt(filters, groupIndex, replaceChild(group, childIndex, child))
}

/**
 * Change a condition's field, resetting the operator and value with it.
 *
 * Carrying the old operator over would routinely produce an invalid pair
 * (`contains` on a Boolean), and carrying the old value over is worse: a
 * monetary string silently reused as a select option id would be refused for
 * a reason that has nothing to do with what the user just did.
 */
export function withField(field: FieldCapability): FilterCondition {
  return conditionFor(field)
}

/** Change the operator, resetting the value to that operator's default shape. */
export function withOperator(
  condition: FilterCondition,
  field: FieldCapability,
  operator: FilterOperator,
): FilterCondition {
  return {
    ...condition,
    operator,
    value: defaultValueFor(field, findOperator(field, operator)),
  }
}

export function withValue(condition: FilterCondition, value: unknown): FilterCondition {
  return { ...condition, value }
}

/** Issues that belong to the node at `path`, for inline display. */
export function issuesAtPath<T extends { path: string }>(issues: T[], path: string): T[] {
  return issues.filter((issue) => issue.path === path || issue.path.startsWith(`${path}.`))
}
