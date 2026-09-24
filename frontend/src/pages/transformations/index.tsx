import { useState } from 'react'
import { useLocation } from 'react-router-dom'

import { transformationsApi } from '@/api/client'
import {
  useCorrespondents, useCustomFields, useDocumentTypes, useFilterCapabilities,
  useFilterValidation, useStoragePaths, useTags,
} from '@/api/queries'
import type { EvaluationResult, FilterSet, SearchSpec, TransformationTarget } from '@/api/types'
import { Button } from '@/components/ui/button'
import { messages } from '@/i18n/messages'
import { FilterBuilder } from '@/pages/explorer/filter-builder'
import { emptyFilterSet, isEmpty } from '@/pages/explorer/filter-builder/model'

import {
  emptyOperation, placeholders, serializeTransformation,
  type OperationDraft, type OperationKind,
} from './model'
import { DryRun } from './dry-run'
import { valueText } from './value'

const INPUT = 'h-9 rounded-md border border-input bg-background px-2 text-sm'
const TARGET_CORE = [
  ['core:title', 'Title'], ['core:correspondent', 'Correspondent ID'],
  ['core:document_type', 'Document type ID'], ['core:storage_path', 'Storage path ID'],
  ['core:tags', 'Tag IDs'], ['core:created', 'Created date'],
  ['core:archive_serial_number', 'Archive serial number'],
] as const
const SOURCE_CORE = [
  ['core:title', 'Title'], ['core:created', 'Created date'],
  ['core:added', 'Added date'], ['core:modified', 'Modified date'],
  ['core:archive_serial_number', 'Archive serial number'],
] as const
const EDITABLE = new Set(['string', 'longtext', 'monetary', 'select', 'date', 'boolean', 'integer'])

export function TransformationsRoute() {
  const location = useLocation()
  const state = location.state as { targets?: TransformationTarget } | null
  return <TransformationsPage key={location.key} initialTargets={state?.targets} />
}

export function TransformationsPage({ initialTargets }: { initialTargets?: TransformationTarget | undefined }) {
  const m = messages.transformations
  const fieldsQuery = useCustomFields()
  const fields = fieldsQuery.data ?? []
  const capabilities = useFilterCapabilities()
  const tags = useTags()
  const correspondents = useCorrespondents()
  const documentTypes = useDocumentTypes()
  const storagePaths = useStoragePaths()
  const initialQuery = initialTargets?.source === 'dataset' ? initialTargets.query : null
  const [source, setSource] = useState<'ids' | 'dataset'>(initialTargets?.source ?? 'ids')
  const [ids, setIds] = useState(initialTargets?.source === 'ids' ? initialTargets.document_ids.join(', ') : '')
  const [documentId, setDocumentId] = useState('')
  const [search, setSearch] = useState(initialQuery?.search?.text ?? '')
  const [searchMode, setSearchMode] = useState<'title' | 'content' | 'advanced'>(initialQuery?.search?.mode ?? 'title')
  const [filters, setFilters] = useState<FilterSet>(initialQuery?.filters ?? emptyFilterSet)
  const [ordering, setOrdering] = useState(initialQuery?.ordering ?? '')
  const validation = useFilterValidation(filters, source === 'dataset' && !isEmpty(filters))
  const [drafts, setDrafts] = useState<OperationDraft[]>([emptyOperation()])
  const [result, setResult] = useState<EvaluationResult | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const customOptions = fields.filter((field) => EDITABLE.has(field.data_type))
    .map((field) => [`custom_field:${field.id}`, field.name] as const)
  const targets = [...TARGET_CORE, ...customOptions]
  const sources = [...SOURCE_CORE, ...customOptions]

  function update(index: number, patch: Partial<OperationDraft>) {
    setDrafts((current) => current.map((draft, position) =>
      position === index ? { ...draft, ...patch } : draft))
    setResult(null)
  }

  function buildTransformation() {
    const searchSpec: SearchSpec | null = search.trim() ? { mode: searchMode, text: search } : null
    if (source === 'dataset' && !isEmpty(filters) && validation.data?.compilable !== true) {
      throw new Error(m.invalidFilters)
    }
    return serializeTransformation(source, ids, searchSpec, filters, drafts, fields, ordering)
  }

  async function evaluate() {
    setError('')
    setResult(null)
    try {
      const transformation = buildTransformation()
      setBusy(true)
      const validationResult = await transformationsApi.validate(transformation)
      if (!validationResult.valid) {
        throw new Error(validationResult.issues.map((issue) =>
          `${issue.code}: ${issue.message}`).join('; '))
      }
      const id = Number(documentId || (source === 'ids' ? ids.split(',')[0] : ''))
      if (!Number.isSafeInteger(id) || id <= 0) throw new Error(m.documentIdRequired)
      setResult(await transformationsApi.evaluate(id, transformation))
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : m.unknownError)
    } finally {
      setBusy(false)
    }
  }

  return <main className="space-y-6 p-6">
    <header>
      <h1 className="text-2xl font-semibold">{m.title}</h1>
      <p className="text-sm text-muted-foreground">{m.subtitle}</p>
    </header>
    <section className="space-y-3 rounded-lg border p-4" aria-label={m.targets}>
      <h2 className="font-semibold">{m.targets}</h2>
      <label className="flex items-center gap-2"><input type="radio" name="target-source"
        checked={source === 'ids'} onChange={() => setSource('ids')} />{m.explicitIds}</label>
      <label className="flex items-center gap-2"><input type="radio" name="target-source"
        checked={source === 'dataset'} onChange={() => setSource('dataset')} />{m.dataset}</label>
      {source === 'ids' ? <label className="block text-sm">{m.ids}
        <input className={`${INPUT} ml-2 w-64`} value={ids} onChange={(event) => setIds(event.target.value)} />
      </label> : <div className="space-y-3">
        <label className="block text-sm">{m.search}
          <select className={`${INPUT} ml-2`} value={searchMode}
            onChange={(event) => setSearchMode(event.target.value as typeof searchMode)}>
            {(['title', 'content', 'advanced'] as const).map((mode) =>
              <option key={mode} value={mode}>{mode}</option>)}
          </select>
          <input className={`${INPUT} ml-2`} value={search}
            onChange={(event) => setSearch(event.target.value)} />
        </label>
        <label className="block text-sm">{messages.preview.ordering}
          <select className={`${INPUT} ml-2`} value={ordering}
            onChange={(event) => setOrdering(event.target.value)}>
            <option value="">{messages.preview.defaultOrdering}</option>
            {['title', 'created', 'modified', 'added', 'archive_serial_number', 'correspondent', 'document_type',
              ...fields.filter((field) => ['string', 'longtext', 'monetary', 'date'].includes(field.data_type))
                .map((field) => `custom_field_${field.id}`)].flatMap((key) => [key, `-${key}`]).map((key) =>
              <option key={key} value={key}>{key}</option>)}
          </select>
        </label>
        <FilterBuilder filters={filters} onChange={setFilters}
          fields={capabilities.data?.fields ?? []} grouping={capabilities.data?.grouping}
          issues={validation.data?.issues ?? []}
          referenceOptions={{ tag: tags.data ?? [], correspondent: correspondents.data ?? [],
            document_type: documentTypes.data ?? [], storage_path: storagePaths.data ?? [] }} />
      </div>}
    </section>
    <section className="space-y-3" aria-label={m.operations}>
      <h2 className="font-semibold">{m.operations}</h2>
      {drafts.map((draft, index) => {
        const definition = draft.fieldKey.startsWith('custom_field:')
          ? fields.find((field) => `custom_field:${field.id}` === draft.fieldKey) : undefined
        const canReplace = draft.fieldKey === 'core:title' ||
          definition?.data_type === 'string' || definition?.data_type === 'longtext'
        const canTemplate = canReplace
        return <div key={index} className="space-y-3 rounded-lg border p-4">
          <div className="flex flex-wrap gap-2">
            <select aria-label={`${m.operation} ${index + 1}`} className={INPUT} value={draft.operation}
              onChange={(event) => update(index, { operation: event.target.value as OperationKind })}>
              {(['set', 'clear', 'replace', 'template'] as const).map((kind) =>
                <option key={kind} value={kind}>{kind.toUpperCase()}</option>)}
            </select>
            <select aria-label={`${m.field} ${index + 1}`} className={INPUT} value={draft.fieldKey}
              onChange={(event) => update(index, { fieldKey: event.target.value })}>
              {targets.map(([key, label]) => <option key={key} value={key}>{label}</option>)}
            </select>
            <Button type="button" variant="outline" disabled={drafts.length === 1}
              onClick={() => setDrafts((current) => current.filter((_, position) => position !== index))}>
              {m.remove}
            </Button>
          </div>
          {draft.operation === 'set' && <label className="block text-sm">{m.value}
            {definition?.data_type === 'boolean' ? <select className={`${INPUT} ml-2`}
              value={draft.value} onChange={(event) => update(index, { value: event.target.value })}>
              <option value="">{m.choose}</option><option value="true">true</option>
              <option value="false">false</option></select>
              : definition?.data_type === 'select' ? <select className={`${INPUT} ml-2`}
                value={draft.value} onChange={(event) => update(index, { value: event.target.value })}>
                <option value="">{m.choose}</option>
                {(Array.isArray(definition.extra_data.select_options)
                  ? definition.extra_data.select_options : []).map((option) => {
                  const item = option as { id: string; label: string }
                  return <option key={item.id} value={item.id}>{item.label}</option>
                })}</select>
                : <input className={`${INPUT} ml-2`} value={draft.value}
                  onChange={(event) => update(index, { value: event.target.value })} />}
          </label>}
          {draft.operation === 'clear' && draft.fieldKey.startsWith('custom_field:') &&
            <label className="block text-sm">{m.clearState}<select className={`${INPUT} ml-2`}
              value={draft.clearState}
              onChange={(event) => update(index, { clearState: event.target.value as 'absent' | 'null' })}>
              <option value="absent">ABSENT</option><option value="null">NULL</option>
            </select></label>}
          {draft.operation === 'replace' && <div className="flex flex-wrap gap-3 text-sm">
            {!canReplace && <p role="alert">{m.textOnly}</p>}
            <label>{m.find}<input className={`${INPUT} ml-2`} value={draft.find}
              onChange={(event) => update(index, { find: event.target.value })} /></label>
            <label>{m.replacement}<input className={`${INPUT} ml-2`} value={draft.replacement}
              onChange={(event) => update(index, { replacement: event.target.value })} /></label>
          </div>}
          {draft.operation === 'template' && <div className="space-y-2 text-sm">
            {!canTemplate && <p role="alert">{m.textOnly}</p>}
            <label className="block">{m.template}<input className={`${INPUT} ml-2 w-full max-w-xl`}
              value={draft.template} onChange={(event) => update(index, { template: event.target.value })} /></label>
            {placeholders(draft.template).map((name) => <label key={name} className="block">
              {`{${name}}`}<select className={`${INPUT} ml-2`} value={draft.bindings[name] ?? ''}
                onChange={(event) => update(index, { bindings: { ...draft.bindings, [name]: event.target.value } })}>
                <option value="">{m.choose}</option>
                {sources.map(([key, label]) => <option key={key} value={key}>{label}</option>)}
              </select></label>)}
          </div>}
        </div>
      })}
      <Button type="button" variant="outline" onClick={() => setDrafts((current) =>
        [...current, emptyOperation()])}>{m.add}</Button>
    </section>
    <DryRun key={JSON.stringify([source, ids, search, searchMode, filters, ordering, drafts, fields])}
      build={buildTransformation} />
    <section className="space-y-3 rounded-lg border p-4">
      <h2 className="font-semibold">{m.evaluate}</h2>
      <p className="text-sm text-muted-foreground">{m.oneDocument}</p>
      <label className="text-sm">{m.documentId}<input className={`${INPUT} ml-2`} value={documentId}
        onChange={(event) => setDocumentId(event.target.value)} /></label>
      <Button type="button" disabled={busy} onClick={evaluate}>{busy ? m.evaluating : m.evaluate}</Button>
      {error && <p role="alert" className="text-destructive">{error}</p>}
      {result && <div className="space-y-2" aria-label={m.results}>
        {result.changes.map((change, index) => <div key={index} className="rounded border p-3 text-sm">
          <strong>{change.field.source === 'core' ? change.field.name : change.field.display_name}</strong>
          <span className="ml-2">{change.status.toUpperCase()}</span>
          {change.issue ? <p role="alert">{change.issue.code}: {change.issue.message}</p>
            : <p>{m.before}: {valueText(change.before)} → {m.intended}: {valueText(change.intended)}</p>}
        </div>)}
      </div>}
    </section>
  </main>
}
