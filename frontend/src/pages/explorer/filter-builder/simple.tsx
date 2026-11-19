import { useId, useState } from 'react'

import type { FieldCapability, FilterNode, FilterSet } from '@/api/types'
import { Button } from '@/components/ui/button'
import { messages } from '@/i18n/messages'

import { conditionFor, fieldKey, findOperator, withOperator, withValue } from './model'
import { ValueInput, type ReferenceOption } from './value-input'

const quickKeys = ['core:document_type', 'core:correspondent', 'core:tags', 'core:created', 'core:added']
const simpleOperators = ['equals', 'contains', 'greater_or_equal', 'less_or_equal', 'has_all_of', 'has_any_of']

function sameUnfilled(node: FilterNode, expression: FilterNode): boolean {
  if (node.kind === 'condition' && expression.kind === 'condition') {
    return fieldKey(node) === fieldKey(expression) && node.operator === expression.operator && node.value == null
  }
  return node.kind === 'group' && expression.kind === 'group' && node.operator === expression.operator
    && node.children.length === expression.children.length
    && node.children.every((child, index) => sameUnfilled(child, expression.children[index]))
}

function simpleField(node: FilterNode, fields: FieldCapability[]) {
  return fields.find((field) => {
    if (field.unfilled?.expression && sameUnfilled(node, field.unfilled.expression)) return true
    return node.kind === 'condition' && fieldKey(node) === field.key
      && simpleOperators.includes(node.operator) && findOperator(field, node.operator) !== undefined
      && (quickKeys.includes(field.key) || field.source === 'custom_field')
  })
}

export function canEditSimply(filters: FilterSet, fields: FieldCapability[]) {
  return filters.root.operator === 'and' && filters.root.children.every((node) => simpleField(node, fields))
}

function initialCondition(field: FieldCapability) {
  const first = field.operators.find((operator) => simpleOperators.includes(operator.operator))
  if (first) return withOperator(conditionFor(field), field, first.operator)
  return field.unfilled?.expression ?? conditionFor(field)
}

function SimpleCondition({ node, field, references, replace, remove }: {
  node: FilterNode
  field: FieldCapability
  references: ReferenceOption[]
  replace: (node: FilterNode) => void
  remove: () => void
}) {
  const [search, setSearch] = useState('')
  const id = useId()
  const unfilled = field.unfilled?.expression
  const condition = node.kind === 'condition' && !(unfilled && sameUnfilled(node, unfilled)) ? node : null
  const operator = condition ? findOperator(field, condition.operator) : undefined
  const selectedIds = Array.isArray(condition?.value) ? condition.value : [condition?.value]
  const visible = references.filter((option) =>
    selectedIds.includes(option.id) || option.name.toLocaleLowerCase().includes(search.toLocaleLowerCase()),
  )

  return <div className="flex flex-wrap items-center gap-2 rounded-md border p-2">
    <label className="rounded-full bg-muted px-2 py-1 text-sm font-medium" htmlFor={id}>{field.label}</label>
    <select id={id} className="h-9 rounded border bg-background px-2 text-sm" value={condition?.operator ?? 'unfilled'}
      onChange={(event) => {
        const next = field.operators.find((item) => item.operator === event.target.value)
        if (next) replace(withOperator(conditionFor(field), field, next.operator))
        if (event.target.value === 'unfilled' && unfilled) replace(structuredClone(unfilled))
      }}>
      {field.operators.filter((item) => simpleOperators.includes(item.operator)).map((item) =>
        <option key={item.operator} value={item.operator}>{item.label}</option>)}
      {unfilled && <option value="unfilled">{messages.filters.unfilled}</option>}
    </select>
    {operator?.value_shape === 'reference_id' && references.length > 0 &&
      <input type="search" className="h-9 w-36 rounded border bg-background px-2 text-sm"
        aria-label={messages.filters.findChoice + ' ' + field.label} placeholder={messages.filters.findChoice}
        value={search} onChange={(event) => setSearch(event.target.value)} />}
    {condition && operator && <ValueInput field={field} operator={operator} value={condition.value}
      referenceOptions={visible} onChange={(value) => replace(withValue(condition, value))} />}
    <Button type="button" size="sm" variant="ghost" onClick={remove}
      aria-label={messages.filters.removeCondition + ' ' + field.label}>{messages.filters.removeCondition}</Button>
  </div>
}

export function SimpleFilters({ filters, fields, referenceOptions, onChange }: {
  filters: FilterSet
  fields: FieldCapability[]
  referenceOptions: Record<string, ReferenceOption[]>
  onChange: (filters: FilterSet) => void
}) {
  const quick = quickKeys.flatMap((key) => fields.filter((field) => field.key === key))
  const custom = fields.filter((field) => field.source === 'custom_field'
    && (field.unfilled?.expression || field.operators.some((op) => simpleOperators.includes(op.operator))))
  function append(node: FilterNode) {
    onChange({ root: { ...filters.root, children: [...filters.root.children, node] } })
  }
  const correspondent = fields.find((field) => field.key === 'core:correspondent')?.unfilled?.expression
  return <div className="space-y-3">
    <div className="flex flex-wrap gap-2">
      {quick.map((field) => <Button key={field.key} type="button" size="sm" variant="outline"
        onClick={() => append(initialCondition(field))}>{field.label}</Button>)}
      {custom.length > 0 && <select className="h-9 rounded border bg-background px-2 text-sm"
        aria-label={messages.filters.addCustom} value="" onChange={(event) => {
          const field = custom.find((item) => item.key === event.target.value)
          if (field) append(initialCondition(field))
        }}>
        <option value="">{messages.filters.addCustom}</option>
        {custom.map((field) => <option key={field.key} value={field.key}>{field.label}</option>)}
      </select>}
    </div>
    {filters.root.children.length === 0 && correspondent && <Button type="button" variant="ghost" size="sm"
      onClick={() => append(structuredClone(correspondent))}>{messages.filters.noCorrespondent}</Button>}
    {filters.root.children.length > 1 && <p className="text-sm">{messages.filters.allConditions}</p>}
    {filters.root.children.map((node, index) => {
      const field = simpleField(node, fields)
      return field && <SimpleCondition key={field.key + ':' + index} node={node} field={field}
        references={referenceOptions[field.reference_kind ?? ''] ?? []}
        replace={(replacement) => onChange({ root: { ...filters.root,
          children: filters.root.children.map((child, at) => at === index ? replacement : child) } })}
        remove={() => onChange({ root: { ...filters.root,
          children: filters.root.children.filter((_, at) => at !== index) } })} />
    })}
  </div>
}
