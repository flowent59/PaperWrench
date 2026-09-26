import * as React from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useParams } from 'react-router'

import { collectionsApi } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { messages } from '@/i18n/messages'

const m = messages.collections

export function CollectionsPage() {
  const queryClient = useQueryClient()
  const listing = useQuery({ queryKey: ['collections'], queryFn: collectionsApi.list })
  const [name, setName] = React.useState('')
  const [error, setError] = React.useState('')
  const [busy, setBusy] = React.useState(false)

  async function create() {
    setBusy(true); setError('')
    try {
      await collectionsApi.create({ name, description: null, document_ids: [] })
      setName('')
      await queryClient.invalidateQueries({ queryKey: ['collections'] })
    } catch (cause) { setError(cause instanceof Error ? cause.message : m.error) }
    finally { setBusy(false) }
  }

  return <div className="space-y-4 p-6">
    <h1 className="text-2xl font-semibold">{m.title}</h1>
    <div className="flex gap-2">
      <input aria-label={m.name} value={name} onChange={event => setName(event.target.value)}
        className="rounded border bg-background px-2" placeholder={m.name} />
      <Button disabled={!name.trim() || busy} onClick={create}>{m.create}</Button>
    </div>
    {error && <p role="alert" className="text-destructive">{error}</p>}
    {listing.isPending ? <p>{m.loading}</p> : listing.isError ? <p role="alert">{m.error}</p> :
      listing.data.length === 0 ? <p>{m.empty}</p> :
      <ul className="space-y-2">{listing.data.map(item =>
        <li key={item.id}><Link className="underline" to={`/collections/${item.id}`}>
          {item.name}</Link> · {m.count(item.member_count)}</li>)}</ul>}
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
    } catch (cause) { setError(cause instanceof Error ? cause.message : m.error) }
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
          <Button size="sm" variant="ghost" disabled={busy}
            onClick={() => act(() => collectionsApi.removeMembers(id, [member.document_id]))}>
            {m.remove}</Button>
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
