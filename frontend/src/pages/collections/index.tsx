import * as React from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useParams } from 'react-router'

import { collectionsApi } from '@/api/client'
import {
  useCorrespondents,
  useDocumentTypes,
  useFilterCapabilities,
  useFilterValidation,
  useStoragePaths,
  useTags,
} from '@/api/queries'
import type { FilterSet } from '@/api/types'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { localizeFilterCapabilities } from '@/i18n/filter-capabilities'
import { messages } from '@/i18n/messages'
import { errorMessage } from '@/i18n/errors'
import { FilterBuilder } from '@/pages/explorer/filter-builder'
import { emptyFilterSet, isEmpty } from '@/pages/explorer/filter-builder/model'

const m = messages.collections

function DynamicCollectionCreator({ name, onCreated }: {
  name: string
  onCreated(): Promise<void>
}) {
  const [filters, setFilters] = React.useState<FilterSet>(emptyFilterSet)
  const [preview, setPreview] = React.useState<Awaited<ReturnType<typeof collectionsApi.preview>> | null>(null)
  const [busy, setBusy] = React.useState(false)
  const [error, setError] = React.useState('')
  const capabilities = useFilterCapabilities()
  const localized = localizeFilterCapabilities(capabilities.data)
  const validation = useFilterValidation(filters, !isEmpty(filters))
  const tags = useTags()
  const correspondents = useCorrespondents()
  const documentTypes = useDocumentTypes()
  const storagePaths = useStoragePaths()
  const referenceOptions = {
    tag: tags.data ?? [], correspondent: correspondents.data ?? [],
    document_type: documentTypes.data ?? [], storage_path: storagePaths.data ?? [],
  }
  const runnable = isEmpty(filters) || validation.data?.compilable === true

  function change(next: FilterSet) {
    setFilters(next)
    setPreview(null)
  }

  async function runPreview() {
    setBusy(true); setError('')
    try { setPreview(await collectionsApi.preview(filters)) }
    catch (cause) { setError(errorMessage(cause, m.error)) }
    finally { setBusy(false) }
  }

  async function create() {
    setBusy(true); setError('')
    try {
      await collectionsApi.create({ name, description: null, kind: 'dynamic', filters })
      await onCreated()
      setFilters(emptyFilterSet())
      setPreview(null)
    } catch (cause) { setError(errorMessage(cause, m.error)) }
    finally { setBusy(false) }
  }

  return <Card><CardContent className="space-y-3 p-4">
    <p className="text-sm text-muted-foreground">{m.dynamicHelp}</p>
    <FilterBuilder filters={filters} onChange={change} fields={localized?.fields ?? []}
      grouping={localized?.grouping} issues={validation.data?.issues ?? []}
      referenceOptions={referenceOptions} />
    <div className="flex items-center gap-2">
      <Button variant="outline" disabled={busy || !runnable} onClick={runPreview}>{m.preview}</Button>
      <Button disabled={busy || !name.trim() || preview === null} onClick={create}>{m.create}</Button>
      {preview && <span>{m.count(preview.total)}</span>}
    </div>
    {preview && <ul className="text-sm">{preview.items.map(item =>
      <li key={item.document_id}>#{item.document_id} · {item.document?.title}</li>)}</ul>}
    {error && <p role="alert" className="text-destructive">{error}</p>}
  </CardContent></Card>
}

export function CollectionsPage() {
  const queryClient = useQueryClient()
  const listing = useQuery({ queryKey: ['collections'], queryFn: collectionsApi.list })
  const [name, setName] = React.useState('')
  const [error, setError] = React.useState('')
  const [busy, setBusy] = React.useState(false)
  const [kind, setKind] = React.useState<'static' | 'dynamic'>('static')

  async function create() {
    setBusy(true); setError('')
    try {
      await collectionsApi.create({ name, description: null, document_ids: [] })
      setName('')
      await queryClient.invalidateQueries({ queryKey: ['collections'] })
    } catch (cause) { setError(errorMessage(cause, m.error)) }
    finally { setBusy(false) }
  }

  return <div className="space-y-4 p-6">
    <h1 className="text-2xl font-semibold">{m.title}</h1>
    <div className="flex gap-2">
      <input aria-label={m.name} value={name} onChange={event => setName(event.target.value)}
        className="rounded border bg-background px-2" placeholder={m.name} />
      <select aria-label={m.kind} value={kind} onChange={event => setKind(event.target.value as 'static' | 'dynamic')}
        className="rounded border bg-background px-2">
        <option value="static">{m.static}</option><option value="dynamic">{m.dynamic}</option>
      </select>
      {kind === 'static' && <Button disabled={!name.trim() || busy} onClick={create}>{m.create}</Button>}
    </div>
    {kind === 'dynamic' && <DynamicCollectionCreator name={name} onCreated={async () => {
      setName(''); await queryClient.invalidateQueries({ queryKey: ['collections'] })
    }} />}
    {error && <p role="alert" className="text-destructive">{error}</p>}
    {listing.isPending ? <p>{m.loading}</p> : listing.isError ? <p role="alert">{m.error}</p> :
      listing.data.length === 0 ? <p>{m.empty}</p> :
      <ul className="space-y-2">{listing.data.map(item =>
        <li key={item.id}><Link className="underline" to={`/collections/${item.id}`}>
          {item.name}</Link> · {item.kind === 'dynamic' ? `${m.dynamic} · ` : ''}{m.count(item.member_count)}</li>)}</ul>}
  </div>
}

export function CollectionPage() {
  const { collectionId } = useParams()
  const navigate = useNavigate()
  const id = Number(collectionId)
  const queryClient = useQueryClient()
  const [page, setPage] = React.useState(1)
  const [editedName, setName] = React.useState<string | null>(null)
  const [editedDescription, setDescription] = React.useState<string | null>(null)
  const [error, setError] = React.useState('')
  const [busy, setBusy] = React.useState(false)
  const listing = useQuery({ queryKey: ['collections'], queryFn: collectionsApi.list })
  const members = useQuery({ queryKey: ['collections', id, 'members', page],
    queryFn: () => collectionsApi.members(id, page), enabled: Number.isInteger(id) && id > 0 })
  const collection = listing.data?.find(item => item.id === id)
  const name = editedName ?? collection?.name ?? ''
  const description = editedDescription ?? collection?.description ?? ''

  async function act(operation: () => unknown) {
    setBusy(true); setError('')
    try {
      await operation()
      await queryClient.invalidateQueries({ queryKey: ['collections'] })
    } catch (cause) { setError(errorMessage(cause, m.error)) }
    finally { setBusy(false) }
  }

  return <div className="space-y-4 p-6">
    <Link className="underline" to="/collections">{m.back}</Link>
    <h1 className="text-2xl font-semibold">{collection?.name ?? m.title}</h1>
    {listing.isError && <p role="alert">{m.error}</p>}
    {listing.isSuccess && !collection && <p role="alert">{m.notFound}</p>}
    {collection && <div className="flex flex-wrap gap-2">
      <input aria-label={m.name} value={name} onChange={event => setName(event.target.value)}
        className="rounded border bg-background px-2" />
      <input aria-label={m.description} value={description} onChange={event => setDescription(event.target.value)}
        className="rounded border bg-background px-2" />
      <Button disabled={busy || !name.trim()} onClick={() => act(() => collectionsApi.update(id, {
        name, description: description || null }))}>{m.save}</Button>
      <Button variant="outline" disabled={busy} onClick={() => act(async () => {
        await collectionsApi.remove(id)
        navigate('/collections')
      })}>{m.delete}</Button>
    </div>}
    {error && <p role="alert" className="text-destructive">{error}</p>}
    <Card><CardContent className="space-y-2 p-4">
      <h2 className="font-semibold">{m.members}</h2>
      {members.isPending ? <p>{m.loading}</p> : members.isError ? <p role="alert">{m.error}</p> :
        members.data.items.length === 0 ? <p>{m.emptyMembers}</p> :
        <ul className="space-y-2">{members.data.items.map(member => <li key={member.document_id}
          className="flex items-center gap-3">
          {member.document ? <Link className="underline" to={`/documents/${member.document_id}`}>
            #{member.document_id} · {member.document.title}</Link> :
            <span>#{member.document_id} · {m.unavailable}</span>}
          {collection?.kind !== 'dynamic' && <Button size="sm" variant="ghost" disabled={busy}
            onClick={() => act(() => collectionsApi.removeMembers(id, [member.document_id]))}>
            {m.remove}</Button>}
        </li>)}</ul>}
      {members.data && <div className="flex items-center gap-2">
        <Button size="sm" variant="outline" disabled={page <= 1} onClick={() => setPage(page - 1)}>{m.previous}</Button>
        <span>{m.page(page, members.data.page_count)}</span>
        <Button size="sm" variant="outline" disabled={page >= members.data.page_count}
          onClick={() => setPage(page + 1)}>{m.next}</Button>
      </div>}
    </CardContent></Card>
  </div>
}
