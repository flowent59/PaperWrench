/** Named document schemas: server-compiled scope and read-only conformance. */

import { useQuery } from '@tanstack/react-query'
import * as React from 'react'
import { Link } from 'react-router'

import { schemasApi } from '@/api/client'
import {
  useCorrespondents,
  useDocumentTypes,
  useFilterCapabilities,
  useFilterValidation,
  useStoragePaths,
  useTags,
} from '@/api/queries'
import type {
  DocumentSchema,
  FieldCapability,
  FieldRef,
  FilterSet,
  SchemaDefinition,
  SchemaEvaluationPage,
  SchemaRule,
} from '@/api/types'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { FilterBuilder } from '@/pages/explorer/filter-builder'
import { emptyFilterSet, isEmpty } from '@/pages/explorer/filter-builder/model'
import { ValueInput } from '@/pages/explorer/filter-builder/value-input'
import type { ReferenceOption } from '@/pages/explorer/filter-builder/value-input'
import { messages } from '@/i18n/messages'
import { localizeFilterCapabilities } from '@/i18n/filter-capabilities'
import { errorMessage } from '@/i18n/errors'

const inputClass = 'h-9 rounded-md border border-input bg-background px-2 text-sm'
const m = messages.schemas

function blank(): SchemaDefinition {
  return { name: '', description: null, applies_when: { filters: emptyFilterSet() }, rules: [] }
}

function key(ref: FieldRef): string {
  return ref.source === 'core' ? `core:${ref.name}` : `custom_field:${ref.field_id}`
}

function refFor(field: FieldCapability): FieldRef {
  return field.source === 'core'
    ? { source: 'core', name: field.key.slice(5) }
    : { source: 'custom_field', field_id: field.custom_field_id as number, display_name: field.label }
}

function canEqual(field: FieldCapability): boolean {
  return !['tag_set', 'document_link'].includes(field.field_type)
}

function initialValue(field: FieldCapability): unknown {
  if (field.field_type === 'boolean') return false
  if (field.field_type === 'select') return field.select_options[0]?.id ?? ''
  if (['integer', 'float', 'reference'].includes(field.field_type)) return 0
  return ''
}

function newRule(field: FieldCapability, kind: 'required' | 'equals'): SchemaRule {
  const base = { field: refFor(field), field_type: field.field_type }
  return kind === 'required'
    ? { kind, ...base }
    : { kind, ...base, value: initialValue(field) }
}

function errorText(error: unknown): string {
  return errorMessage(error, m.requestFailed)
}

export function SchemasPage() {
  const listing = useQuery({ queryKey: ['schemas'], queryFn: schemasApi.list })
  const capabilities = useFilterCapabilities()
  const tags = useTags()
  const correspondents = useCorrespondents()
  const documentTypes = useDocumentTypes()
  const storagePaths = useStoragePaths()
  const [selected, setSelected] = React.useState<number | null>(null)
  const [draft, setDraft] = React.useState<SchemaDefinition>(blank)
  const [page, setPage] = React.useState(1)
  const [evaluation, setEvaluation] = React.useState<SchemaEvaluationPage | null>(null)
  const [busy, setBusy] = React.useState(false)
  const [error, setError] = React.useState('')

  const localizedCapabilities = localizeFilterCapabilities(capabilities.data)
  const fields = localizedCapabilities?.fields ?? []
  const persisted = listing.data?.find(schema => schema.id === selected)
  const dirty = selected !== null && persisted !== undefined &&
    JSON.stringify({ name: persisted.name, description: persisted.description,
      applies_when: persisted.applies_when, rules: persisted.rules }) !== JSON.stringify(draft)
  const filters = draft.applies_when.filters ?? emptyFilterSet()
  const validation = useFilterValidation(filters, !isEmpty(filters))
  const filterReady = isEmpty(filters) || validation.data?.compilable === true
  const referenceOptions = React.useMemo<Record<string, ReferenceOption[]>>(() => ({
    tag: tags.data ?? [], correspondent: correspondents.data ?? [],
    document_type: documentTypes.data ?? [], storage_path: storagePaths.data ?? [],
  }), [tags.data, correspondents.data, documentTypes.data, storagePaths.data])

  function selectSchema(schema: DocumentSchema) {
    setSelected(schema.id)
    setDraft({
      name: schema.name, description: schema.description,
      applies_when: schema.applies_when, rules: schema.rules,
    })
    setEvaluation(null)
    setPage(1)
    setError('')
  }

  async function save() {
    setBusy(true)
    setError('')
    try {
      const saved = selected === null
        ? await schemasApi.create(draft)
        : await schemasApi.update(selected, draft)
      selectSchema(saved)
      await listing.refetch()
    } catch (cause) {
      setError(errorText(cause))
    } finally {
      setBusy(false)
    }
  }

  async function remove() {
    if (selected === null) return
    setBusy(true)
    setError('')
    try {
      await schemasApi.remove(selected)
      setSelected(null)
      setDraft(blank())
      setEvaluation(null)
      await listing.refetch()
    } catch (cause) {
      setError(errorText(cause))
    } finally {
      setBusy(false)
    }
  }

  async function evaluate(targetPage: number) {
    if (selected === null) return
    setBusy(true)
    setError('')
    setEvaluation(null)
    try {
      const result = await schemasApi.evaluate(selected, targetPage)
      setPage(targetPage)
      setEvaluation(result)
    } catch (cause) {
      setError(errorText(cause))
    } finally {
      setBusy(false)
    }
  }

  function changeRule(index: number, rule: SchemaRule) {
    setDraft(current => ({
      ...current, rules: current.rules.map((item, position) => position === index ? rule : item),
    }))
  }

  return (
    <div className="space-y-6 p-6">
      <div>
        <h1 className="text-2xl font-semibold">{m.title}</h1>
        <p className="text-sm text-muted-foreground">{m.subtitle}</p>
      </div>

      <Card><CardContent className="space-y-3 pt-6">
        <div className="flex flex-wrap gap-2">
          <Button onClick={() => { setSelected(null); setDraft(blank()); setEvaluation(null); setError('') }}>{m.new}</Button>
          {listing.data?.map(schema =>
            <Button key={schema.id} variant={schema.id === selected ? 'default' : 'outline'}
              onClick={() => selectSchema(schema)}>{schema.name}</Button>) }
        </div>
        {listing.isError && <p role="alert">{errorText(listing.error)}</p>}
      </CardContent></Card>

      <Card><CardContent className="space-y-5 pt-6">
        <h2 className="text-lg font-semibold">{selected === null ? m.create : m.edit}</h2>
        <label className="block space-y-1 text-sm">{m.name}
          <input className={`${inputClass} block w-full`} value={draft.name}
            onChange={event => setDraft({ ...draft, name: event.target.value })} />
        </label>
        <label className="block space-y-1 text-sm">{m.description}
          <textarea className={`${inputClass} block h-20 w-full py-2`} value={draft.description ?? ''}
            onChange={event => setDraft({ ...draft, description: event.target.value || null })} />
        </label>

        <section className="space-y-3">
          <h3 className="font-semibold">{m.appliesWhen}</h3>
          <p className="text-sm text-muted-foreground">{m.scopeHint}</p>
          <label className="block space-y-1 text-sm">{m.titleSearch}
            <input className={`${inputClass} block w-full`}
              value={draft.applies_when.search?.text ?? ''}
              onChange={event => setDraft(current => ({ ...current, applies_when: {
                ...current.applies_when,
                search: event.target.value ? { mode: 'title', text: event.target.value } : null,
              } }))} />
          </label>
          {localizedCapabilities && <FilterBuilder
            filters={filters}
            onChange={(next: FilterSet) => setDraft(current => ({
              ...current, applies_when: { ...current.applies_when, filters: next },
            }))}
            fields={fields} grouping={localizedCapabilities.grouping}
            issues={validation.data?.issues ?? []} referenceOptions={referenceOptions}
          />}
          {capabilities.isError && <p role="alert">{errorText(capabilities.error)}</p>}
          {!isEmpty(filters) && validation.data && !validation.data.compilable &&
            <p role="alert" className="text-sm text-destructive">{m.invalidScope}</p>}
        </section>

        <section className="space-y-3">
          <h3 className="font-semibold">{m.rules}</h3>
          <p className="text-sm text-muted-foreground">{m.requiredHint}</p>
          {draft.rules.map((rule, index) => {
            const field = fields.find(candidate => candidate.key === key(rule.field))
            const equality = field?.operators.find(operator => operator.operator === 'equals')
            return <div key={index} className="flex flex-wrap items-center gap-2 rounded-md border p-3">
              <select aria-label={m.ruleField(index + 1)} className={inputClass} value={key(rule.field)}
                onChange={event => {
                  const next = fields.find(candidate => candidate.key === event.target.value)
                  if (next) changeRule(index, newRule(next, rule.kind === 'equals' && canEqual(next) ? 'equals' : 'required'))
                }}>
                {!field && <option value={key(rule.field)}>{m.unavailable} {key(rule.field)}</option>}
                {fields.map(candidate => <option key={candidate.key} value={candidate.key}>{candidate.label}</option>)}
              </select>
              <select aria-label={m.ruleKind(index + 1)} className={inputClass} value={rule.kind}
                onChange={event => { if (field) changeRule(index, newRule(field, event.target.value as 'required' | 'equals')) }}>
                <option value="required">{m.required}</option>
                {field && canEqual(field) && <option value="equals">{m.equals}</option>}
              </select>
              {rule.kind === 'equals' && field && equality &&
                <ValueInput field={field} operator={equality} value={rule.value}
                  referenceOptions={referenceOptions[field.reference_kind ?? ''] ?? []}
                  onChange={value => changeRule(index, { ...rule, value })} />}
              <Button variant="outline" onClick={() => setDraft(current => ({
                ...current, rules: current.rules.filter((_, position) => position !== index),
              }))}>{m.removeRule}</Button>
            </div>
          })}
          <Button variant="outline" disabled={fields.length === 0} onClick={() => {
            const first = fields[0]
            if (first) setDraft(current => ({ ...current, rules: [...current.rules, newRule(first, 'required')] }))
          }}>{m.addRule}</Button>
        </section>

        {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
        <div className="flex gap-2">
          <Button disabled={busy || !filterReady || draft.rules.length === 0 || !draft.name.trim()}
            onClick={save}>{m.save}</Button>
          {selected !== null && <Button variant="outline" disabled={busy} onClick={remove}>{m.delete}</Button>}
        </div>
      </CardContent></Card>

      {selected !== null && <Card><CardContent className="space-y-3 pt-6">
        <div className="flex items-center gap-3">
          <h2 className="text-lg font-semibold">{m.conformance}</h2>
          <Button disabled={busy || dirty} onClick={() => evaluate(1)}>{m.evaluate}</Button>
        </div>
        {dirty && <p className="text-sm text-muted-foreground">{m.saveBeforeEvaluation}</p>}
        {evaluation && <>
          <p className="text-sm">{m.matching(evaluation.total, evaluation.page, evaluation.page_count)}</p>
          <ul className="space-y-2">
            {evaluation.items.map(item => <li key={item.document_id} className="rounded-md border p-3">
              <Link className="font-medium underline" to={`/documents/${item.document_id}`}>{item.title}</Link>
              <span className="ml-3">{item.status}</span>
              <ul className="ml-4 list-disc text-sm">
                {item.rules.map(rule => <li key={rule.rule_index}>
                  {key(rule.field)} · {rule.kind} · {rule.status} · {rule.value_kind}
                  {rule.code && ` (${rule.code})`}
                </li>)}
              </ul>
            </li>)}
          </ul>
          <div className="flex gap-2">
            <Button variant="outline" disabled={busy || page <= 1} onClick={() => evaluate(page - 1)}>{m.previous}</Button>
            <Button variant="outline" disabled={busy || page >= evaluation.page_count} onClick={() => evaluate(page + 1)}>{m.next}</Button>
          </div>
        </>}
      </CardContent></Card>}
    </div>
  )
}
