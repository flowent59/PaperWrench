import { describe, expect, it } from 'vitest'

import type { FieldCapability, FilterSet, GroupingCapabilities } from '@/api/types'

import {
  addCondition,
  addOrGroup,
  addToGroup,
  conditionFor,
  defaultValueFor,
  emptyFilterSet,
  fieldKey,
  findField,
  findOperator,
  groupingAllows,
  isEmpty,
  issuesAtPath,
  removeAt,
  removeFromGroup,
  selectableFields,
  updateAt,
  updateInGroup,
  withField,
  withOperator,
  withValue,
} from './model'

const TITLE: FieldCapability = {
  key: 'core:title',
  label: 'Title',
  field_type: 'text',
  source: 'core',
  custom_field_id: null,
  reference_kind: null,
  select_options: [],
  operators: [
    { operator: 'contains', label: 'contains', value_shape: 'text', multi: false, note: null },
    { operator: 'equals', label: 'is', value_shape: 'text', multi: false, note: 'Case-insensitive.' },
  ],
}

const TAGS: FieldCapability = {
  key: 'core:tags',
  label: 'Tags',
  field_type: 'tag_set',
  source: 'core',
  custom_field_id: null,
  reference_kind: 'tag',
  select_options: [],
  operators: [
    {
      operator: 'has_all_of',
      label: 'has all of',
      value_shape: 'reference_id',
      multi: true,
      note: null,
    },
  ],
}

const MONTANT: FieldCapability = {
  key: 'custom_field:2',
  label: 'Montant',
  field_type: 'monetary',
  source: 'custom_field',
  custom_field_id: 2,
  reference_kind: null,
  select_options: [],
  operators: [
    {
      operator: 'greater_than',
      label: 'is greater than',
      value_shape: 'decimal',
      multi: false,
      note: null,
    },
    {
      operator: 'is_missing',
      label: 'is missing',
      value_shape: 'none',
      multi: false,
      note: null,
    },
  ],
}

const CATEGORIE: FieldCapability = {
  key: 'custom_field:7',
  label: 'Catégorie',
  field_type: 'select',
  source: 'custom_field',
  custom_field_id: 7,
  reference_kind: null,
  select_options: [
    { id: 'optUrgent0000001', label: 'Urgent' },
    { id: 'optNormal0000001', label: 'Normal' },
  ],
  operators: [
    {
      operator: 'equals',
      label: 'is',
      value_shape: 'select_option',
      multi: false,
      note: null,
    },
  ],
}

const FIELDS = [TITLE, TAGS, MONTANT, CATEGORIE]

const GROUPING: GroupingCapabilities = {
  and_supported: true,
  or_custom_fields_supported: true,
  or_core_fields_supported: false,
  or_mixed_supported: false,
  not_supported: false,
  max_conditions: 50,
  max_depth: 10,
  custom_field_max_depth: 10,
  custom_field_max_conditions: 20,
}

describe('the empty filter', () => {
  it('is a root AND group with no children', () => {
    const filters = emptyFilterSet()
    expect(filters.root.operator).toBe('and')
    expect(filters.root.children).toEqual([])
    expect(isEmpty(filters)).toBe(true)
  })
})

describe('building a condition', () => {
  it('references a core field by name and a custom field by id', () => {
    expect(conditionFor(TITLE).field).toEqual({ source: 'core', name: 'title' })
    expect(conditionFor(MONTANT).field).toEqual({
      source: 'custom_field',
      field_id: 2,
      display_name: 'Montant',
    })
  })

  it('carries the custom field label for display but keys on the id', () => {
    // A rename in Paperless must not change what the filter means, so the
    // id is the identity and the name is decoration.
    expect(fieldKey(conditionFor(MONTANT))).toBe('custom_field:2')
  })

  it('starts on the field first operator', () => {
    expect(conditionFor(TITLE).operator).toBe('contains')
    expect(conditionFor(MONTANT).operator).toBe('greater_than')
  })

  it('leaves a text value null rather than empty', () => {
    // An empty string is a value the backend refuses on purpose (Paperless
    // would drop the filter and return everything), so a fresh row must not
    // look like it already has one.
    expect(conditionFor(TITLE).value).toBeNull()
  })

  it('starts a multi-valued operator with an empty list', () => {
    expect(conditionFor(TAGS).value).toEqual([])
  })

  it('preselects the first option of a select field', () => {
    expect(conditionFor(CATEGORIE).value).toBe('optUrgent0000001')
  })

  it('gives a valueless operator no value at all', () => {
    const isMissing = MONTANT.operators[1]
    expect(defaultValueFor(MONTANT, isMissing)).toBeNull()
  })
})

describe('changing a field or operator', () => {
  it('resets the operator and value when the field changes', () => {
    // Carrying `contains` onto a monetary field would build a pair the
    // backend rejects, for a reason unrelated to what the user just did.
    const before = withValue(conditionFor(TITLE), 'vacations')
    const after = withField(MONTANT)

    expect(after.operator).toBe('greater_than')
    expect(after.value).toBeNull()
    expect(before.value).toBe('vacations')
  })

  it('resets the value when the operator changes shape', () => {
    const before = withValue(conditionFor(MONTANT), '10.00')
    const after = withOperator(before, MONTANT, 'is_missing')

    expect(after.operator).toBe('is_missing')
    expect(after.value).toBeNull()
  })

  it('keeps the field when only the operator changes', () => {
    const after = withOperator(conditionFor(MONTANT), MONTANT, 'is_missing')
    expect(fieldKey(after)).toBe('custom_field:2')
  })
})

describe('what the builder may offer', () => {
  it('offers every field at the top level', () => {
    expect(selectableFields(FIELDS, false, GROUPING)).toHaveLength(4)
  })

  it('offers only custom fields inside an OR group', () => {
    // The one OR shape the compiler can translate. Offering a core field
    // here would invite the user to build something we know will be refused.
    const inGroup = selectableFields(FIELDS, true, GROUPING)
    expect(inGroup.map((field) => field.key)).toEqual([
      'custom_field:2',
      'custom_field:7',
    ])
  })

  it('stops restricting the group if the backend ever compiles a mixed OR', () => {
    const permissive = {
      ...GROUPING,
      or_mixed_supported: true,
      or_core_fields_supported: true,
    }
    expect(selectableFields(FIELDS, true, permissive)).toHaveLength(4)
  })

  it('offers no OR group when the backend does not support one', () => {
    expect(groupingAllows({ ...GROUPING, or_custom_fields_supported: false }).orGroups).toBe(
      false,
    )
  })

  it('offers nothing before the capabilities have loaded', () => {
    expect(groupingAllows(undefined).orGroups).toBe(false)
  })
})

describe('tree edits', () => {
  function withTwo(): FilterSet {
    return addCondition(addCondition(emptyFilterSet(), conditionFor(TITLE)), conditionFor(MONTANT))
  }

  it('appends conditions to the root AND', () => {
    const filters = withTwo()
    expect(filters.root.children).toHaveLength(2)
    expect(filters.root.operator).toBe('and')
  })

  it('never mutates the filter it was given', () => {
    const before = emptyFilterSet()
    addCondition(before, conditionFor(TITLE))
    expect(before.root.children).toHaveLength(0)
  })

  it('removes a condition by index', () => {
    const filters = removeAt(withTwo(), 0)
    expect(filters.root.children).toHaveLength(1)
    expect(fieldKey(filters.root.children[0] as never)).toBe('custom_field:2')
  })

  it('replaces a condition in place', () => {
    const filters = updateAt(withTwo(), 0, withValue(conditionFor(TITLE), 'scan'))
    expect((filters.root.children[0] as { value: unknown }).value).toBe('scan')
  })

  it('adds an OR group carrying its first alternative', () => {
    const filters = addOrGroup(emptyFilterSet(), conditionFor(MONTANT))
    const group = filters.root.children[0]
    expect(group).toMatchObject({ kind: 'group', operator: 'or' })
    expect((group as { children: unknown[] }).children).toHaveLength(1)
  })

  it('adds alternatives to an existing group', () => {
    const filters = addToGroup(
      addOrGroup(emptyFilterSet(), conditionFor(MONTANT)),
      0,
      conditionFor(CATEGORIE),
    )
    expect((filters.root.children[0] as { children: unknown[] }).children).toHaveLength(2)
  })

  it('updates one alternative without touching the others', () => {
    let filters = addOrGroup(emptyFilterSet(), conditionFor(MONTANT))
    filters = addToGroup(filters, 0, conditionFor(CATEGORIE))
    filters = updateInGroup(filters, 0, 1, withValue(conditionFor(CATEGORIE), 'optNormal0000001'))

    const group = filters.root.children[0] as { children: { value: unknown }[] }
    expect(group.children[0]?.value).toBeNull()
    expect(group.children[1]?.value).toBe('optNormal0000001')
  })

  it('collapses a group to a plain condition when one alternative is left', () => {
    // A one-branch OR means the same thing as its branch, and leaving a
    // pointless group behind makes the filter read as more complicated than
    // it is.
    let filters = addOrGroup(emptyFilterSet(), conditionFor(MONTANT))
    filters = addToGroup(filters, 0, conditionFor(CATEGORIE))
    filters = removeFromGroup(filters, 0, 1)

    expect(filters.root.children[0]).toMatchObject({ kind: 'condition' })
    expect(fieldKey(filters.root.children[0] as never)).toBe('custom_field:2')
  })

  it('removes the group entirely when its last alternative goes', () => {
    // An empty nested group is a backend validation error - it is neither
    // "match everything" nor "match nothing".
    const filters = removeFromGroup(addOrGroup(emptyFilterSet(), conditionFor(MONTANT)), 0, 0)
    expect(filters.root.children).toHaveLength(0)
  })

  it('ignores a group edit aimed at a plain condition', () => {
    const filters = addCondition(emptyFilterSet(), conditionFor(TITLE))
    expect(addToGroup(filters, 0, conditionFor(MONTANT))).toBe(filters)
    expect(removeFromGroup(filters, 0, 0)).toBe(filters)
  })
})

describe('looking things up', () => {
  it('finds a field and an operator by key', () => {
    expect(findField(FIELDS, 'custom_field:2')).toBe(MONTANT)
    expect(findOperator(MONTANT, 'is_missing')?.label).toBe('is missing')
  })

  it('returns undefined for a field the catalogue no longer knows', () => {
    // A custom field deleted in Paperless. The row still renders so it can
    // be fixed, rather than the condition silently vanishing.
    expect(findField(FIELDS, 'custom_field:999')).toBeUndefined()
    expect(findOperator(undefined, 'equals')).toBeUndefined()
  })
})

describe('issue routing', () => {
  const issues = [
    { path: 'root.children[0]' },
    { path: 'root.children[1].value' },
    { path: 'root' },
  ]

  it('routes an issue to the node it names', () => {
    expect(issuesAtPath(issues, 'root.children[0]')).toHaveLength(1)
  })

  it('routes a value issue to its owning condition', () => {
    expect(issuesAtPath(issues, 'root.children[1]')).toHaveLength(1)
  })

  it('does not route a whole-filter issue to a row', () => {
    expect(issuesAtPath(issues, 'root.children[2]')).toHaveLength(0)
  })
})
