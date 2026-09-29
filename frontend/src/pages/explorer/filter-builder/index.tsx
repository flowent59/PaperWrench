/**
 * The Filter Builder.
 *
 * Constrained by the *backend's* capabilities, not by a copy of them: the
 * field list, the operator list per field, the value shape of each operator
 * and which grouping shapes are legal all come from
 * `GET /api/v1/filters/capabilities`. Consequences worth stating:
 *
 * - custom fields appear because they exist in Paperless, never because a
 *   name was hardcoded here;
 * - a Boolean field simply has no `contains` in its dropdown;
 * - an OR group only offers custom fields, because a mixed core/custom OR is
 *   the one shape the compiler refuses, and offering it would be inviting the
 *   user to build something we already know will be rejected.
 *
 * The builder is an affordance, not the rule. Every filter still goes to
 * `/filters/validate` on each change, and the verdict shown at the bottom is
 * the backend's - including the case that matters most, "valid but not
 * compilable", which is a limitation of Paperless rather than a user error
 * and is worded differently for exactly that reason.
 */

import { AlertTriangle, Info, Plus, Trash2 } from 'lucide-react'
import * as React from 'react'

import type {
  FieldCapability,
  FilterCondition,
  FilterGroup,
  FilterIssue,
  FilterSet,
  GroupingCapabilities,
} from '@/api/types'
import { Button } from '@/components/ui/button'
import { messages } from '@/i18n/messages'
import { issueMessage } from '@/i18n/errors'
import { cn } from '@/lib/utils'

import {
  addCondition,
  addOrGroup,
  addToGroup,
  conditionFor,
  fieldKey,
  findField,
  findOperator,
  groupingAllows,
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
import { ValueInput, type ReferenceOption } from './value-input'

const SELECT_CLASS =
  'h-9 rounded-md border border-input bg-background px-2 text-sm focus-ring'

export interface FilterBuilderProps {
  filters: FilterSet
  onChange: (filters: FilterSet) => void
  fields: FieldCapability[]
  grouping: GroupingCapabilities | undefined
  issues: FilterIssue[]
  /** Metadata options per `reference_kind`, for reference-valued conditions. */
  referenceOptions: Record<string, ReferenceOption[]>
}

interface RowProps {
  condition: FilterCondition
  path: string
  fields: FieldCapability[]
  issues: FilterIssue[]
  referenceOptions: Record<string, ReferenceOption[]>
  onChange: (condition: FilterCondition) => void
  onRemove: () => void
}

function ConditionRow({
  condition,
  path,
  fields,
  issues,
  referenceOptions,
  onChange,
  onRemove,
}: RowProps) {
  const key = fieldKey(condition)
  const field = findField(fields, key)
  const operator = findOperator(field, condition.operator)
  const rowIssues = issuesAtPath(issues, path)

  return (
    <div className="flex flex-col gap-1">
      <div className="flex flex-wrap items-center gap-2">
        <select
          className={SELECT_CLASS}
          value={key}
          aria-label={messages.filters.field}
          onChange={(event) => {
            const next = findField(fields, event.target.value)
            if (next !== undefined) onChange(withField(next))
          }}
        >
          {/* A field the catalogue no longer knows (deleted in Paperless)
              still renders, so the row can be seen and fixed rather than
              silently disappearing along with the condition it carried. */}
          {field === undefined && <option value={key}>{key}</option>}
          {fields.map((candidate) => (
            <option key={candidate.key} value={candidate.key}>
              {candidate.label}
            </option>
          ))}
        </select>

        <select
          className={SELECT_CLASS}
          value={condition.operator}
          aria-label={messages.filters.operator}
          onChange={(event) => {
            if (field !== undefined) {
              onChange(
                withOperator(
                  condition,
                  field,
                  event.target.value as FilterCondition['operator'],
                ),
              )
            }
          }}
        >
          {(field?.operators ?? []).map((candidate) => (
            <option key={candidate.operator} value={candidate.operator}>
              {candidate.label}
            </option>
          ))}
        </select>

        {field !== undefined && operator !== undefined && (
          <ValueInput
            field={field}
            operator={operator}
            value={condition.value}
            referenceOptions={referenceOptions[field.reference_kind ?? ''] ?? []}
            onChange={(value) => onChange(withValue(condition, value))}
          />
        )}

        <Button
          variant="ghost"
          size="icon"
          onClick={onRemove}
          aria-label={messages.filters.removeCondition}
        >
          <Trash2 className="h-4 w-4" aria-hidden="true" />
        </Button>
      </div>

      {operator?.note != null && (
        <p className="flex items-center gap-1.5 pl-1 text-xs text-muted-foreground">
          <Info className="h-3 w-3 shrink-0" aria-hidden="true" />
          {operator.note}
        </p>
      )}

      {rowIssues.map((issue) => (
        <p key={`${issue.code}-${issue.path}`} className="pl-1 text-xs text-destructive">
          {issueMessage(issue)}
        </p>
      ))}
    </div>
  )
}

export function FilterBuilder({
  filters,
  onChange,
  fields,
  grouping,
  issues,
  referenceOptions,
}: FilterBuilderProps) {
  const { orGroups } = groupingAllows(grouping)
  const groupFields = React.useMemo(
    () => selectableFields(fields, true, grouping),
    [fields, grouping],
  )

  const firstField = fields[0]
  const firstGroupField = groupFields[0]

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-col gap-3">
        {filters.root.children.map((child, index) => {
          const path = `root.children[${index}]`

          if (child.kind === 'condition') {
            return (
              <div key={path} className="flex items-start gap-2">
                <span className="mt-2 w-10 shrink-0 text-xs font-medium uppercase text-muted-foreground">
                  {index === 0 ? messages.filters.where : messages.filters.and}
                </span>
                <ConditionRow
                  condition={child}
                  path={path}
                  fields={fields}
                  issues={issues}
                  referenceOptions={referenceOptions}
                  onChange={(condition) => onChange(updateAt(filters, index, condition))}
                  onRemove={() => onChange(removeAt(filters, index))}
                />
              </div>
            )
          }

          if (child.kind !== 'group') {
            // NOT is representable in the domain model but is not offered by
            // this builder and is not compiled in this version. If one turns
            // up in a stored filter, say so rather than rendering nothing.
            return (
              <p key={path} className="text-sm text-destructive">
                {messages.filters.unsupportedNode}
              </p>
            )
          }

          const group: FilterGroup = child
          return (
            <div key={path} className="flex items-start gap-2">
              <span className="mt-2 w-10 shrink-0 text-xs font-medium uppercase text-muted-foreground">
                {index === 0 ? messages.filters.where : messages.filters.and}
              </span>
              <div className="flex-1 rounded-md border border-dashed border-border p-3">
                <p className="mb-2 text-xs text-muted-foreground">
                  {messages.filters.anyOfTheFollowing}
                </p>
                <div className="flex flex-col gap-3">
                  {group.children.map((grandchild, childIndex) => {
                    const childPath = `${path}.children[${childIndex}]`
                    if (grandchild.kind !== 'condition') {
                      return (
                        <p key={childPath} className="text-sm text-destructive">
                          {messages.filters.unsupportedNode}
                        </p>
                      )
                    }
                    return (
                      <div key={childPath} className="flex items-start gap-2">
                        <span className="mt-2 w-8 shrink-0 text-xs font-medium uppercase text-muted-foreground">
                          {childIndex === 0 ? '' : messages.filters.or}
                        </span>
                        <ConditionRow
                          condition={grandchild}
                          path={childPath}
                          fields={groupFields}
                          issues={issues}
                          referenceOptions={referenceOptions}
                          onChange={(condition) =>
                            onChange(updateInGroup(filters, index, childIndex, condition))
                          }
                          onRemove={() =>
                            onChange(removeFromGroup(filters, index, childIndex))
                          }
                        />
                      </div>
                    )
                  })}
                </div>
                {firstGroupField !== undefined && (
                  <Button
                    variant="ghost"
                    size="sm"
                    className="mt-2"
                    onClick={() =>
                      onChange(addToGroup(filters, index, conditionFor(firstGroupField)))
                    }
                  >
                    <Plus className="h-3.5 w-3.5" aria-hidden="true" />
                    {messages.filters.addAlternative}
                  </Button>
                )}
                <p className="mt-2 text-xs text-muted-foreground">
                  {messages.filters.orGroupCustomFieldsOnly}
                </p>
              </div>
            </div>
          )
        })}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          disabled={firstField === undefined}
          onClick={() => {
            if (firstField !== undefined) {
              onChange(addCondition(filters, conditionFor(firstField)))
            }
          }}
        >
          <Plus className="h-3.5 w-3.5" aria-hidden="true" />
          {messages.filters.addCondition}
        </Button>

        {orGroups && (
          <Button
            variant="outline"
            size="sm"
            disabled={firstGroupField === undefined}
            onClick={() => {
              if (firstGroupField !== undefined) {
                onChange(addOrGroup(filters, conditionFor(firstGroupField)))
              }
            }}
          >
            <Plus className="h-3.5 w-3.5" aria-hidden="true" />
            {messages.filters.addOrGroup}
          </Button>
        )}
      </div>

      {/* Issues that belong to the filter as a whole rather than to one row -
          most often "this shape cannot be compiled". */}
      {issues
        .filter((issue) => issue.path === 'root')
        .map((issue) => (
          <p
            key={issue.code}
            className={cn(
              'flex items-start gap-1.5 text-sm',
              issue.stage === 'compilation' ? 'text-amber-600 dark:text-amber-500' : 'text-destructive',
            )}
          >
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
            {issueMessage(issue)}
          </p>
        ))}
    </div>
  )
}
