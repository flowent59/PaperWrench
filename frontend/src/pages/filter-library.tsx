import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { filterLibraryApi } from '@/api/client'
import { useCorrespondents, useDocumentTypes, useFilterCapabilities, useFilterValidation,
  useStoragePaths, useTags } from '@/api/queries'
import type { FilterLibraryEntry, FilterLibraryInput } from '@/api/types'
import { Button } from '@/components/ui/button'
import { errorMessage } from '@/i18n/errors'
import { localizeFilterCapabilities } from '@/i18n/filter-capabilities'
import { messages } from '@/i18n/messages'
import { FilterBuilder } from '@/pages/explorer/filter-builder'
import { emptyFilterSet } from '@/pages/explorer/filter-builder/model'
import { filterLibraryKey } from '@/pages/explorer/filter-builder/library-picker'

function blank(): FilterLibraryInput {
  return { name: '', description: '', definition: { version: 1, filters: emptyFilterSet() } }
}

export function FilterLibraryPage() {
  const m = messages.filters
  const query = useQueryClient()
  const entries = useQuery({ queryKey: filterLibraryKey, queryFn: filterLibraryApi.list })
  const capabilities = useFilterCapabilities()
  const localized = localizeFilterCapabilities(capabilities.data)
  const tags = useTags()
  const correspondents = useCorrespondents()
  const types = useDocumentTypes()
  const paths = useStoragePaths()
  const [draft, setDraft] = useState(blank)
  const [editing, setEditing] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const validation = useFilterValidation(draft.definition.filters, true)

  function open(entry?: FilterLibraryEntry, copy = false) {
    setEditing(entry && !copy ? entry.id : null)
    setDraft(entry ? {
      name: copy ? '' : entry.name, description: entry.description,
      definition: structuredClone(entry.definition),
    } : blank())
    setError('')
  }

  async function change(action: () => Promise<unknown>) {
    setBusy(true)
    setError('')
    try {
      await action()
      await query.invalidateQueries({ queryKey: filterLibraryKey })
      open()
    } catch (cause) {
      setError(errorMessage(cause))
    } finally {
      setBusy(false)
    }
  }

  return <div className="mx-auto max-w-4xl space-y-4 p-4 sm:p-6">
    <h1 className="text-2xl font-semibold">{messages.nav.filters}</h1>
    <p className="text-sm">{m.libraryHelp}</p>
    {entries.isPending ? <p role="status">{m.counting}</p> : entries.isError
      ? <p role="alert">{errorMessage(entries.error)}</p>
      : entries.data.length === 0 ? <p>{m.libraryEmpty}</p>
      : <ul className="space-y-2">{entries.data.map((entry) => <li key={entry.id}
        className="flex flex-wrap justify-between gap-2 rounded border p-3">
        <div><p className="break-words font-medium">{entry.name}</p><p className="break-words text-sm">{entry.description}</p></div>
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="outline" disabled={busy} onClick={() => open(entry)}>{m.libraryEdit}</Button>
          <Button size="sm" variant="outline" disabled={busy} onClick={() => open(entry, true)}>{m.libraryDuplicate}</Button>
          <Button size="sm" variant="outline" disabled={busy} onClick={() => {
            if (window.confirm(m.libraryConfirm)) void change(() => filterLibraryApi.remove(entry.id))
          }}>{m.libraryRemove}</Button>
        </div>
      </li>)}</ul>}
    <form className="space-y-3 rounded border p-4" onSubmit={(event) => {
      event.preventDefault()
      if (!draft.name.trim() || validation.data?.compilable !== true) return
      void change(() => editing === null ? filterLibraryApi.create(draft) : filterLibraryApi.update(editing, draft))
    }}>
      <h2 className="font-medium">{editing === null ? m.libraryNew : m.libraryEdit}</h2>
      <label className="block">{messages.collections.name}
        <input className="block w-full rounded border bg-background p-2" value={draft.name} maxLength={255}
          required onChange={(event) => setDraft({ ...draft, name: event.target.value })} />
      </label>
      <label className="block">{m.libraryDescription}
        <textarea className="block w-full rounded border bg-background p-2" value={draft.description}
          maxLength={1000} onChange={(event) => setDraft({ ...draft, description: event.target.value })} />
      </label>
      {capabilities.isError && <p role="alert">{errorMessage(capabilities.error)}</p>}
      {localized && <FilterBuilder library={false} filters={draft.definition.filters}
        fields={localized.fields} grouping={localized.grouping} issues={validation.data?.issues ?? []}
        referenceOptions={{ tag: tags.data ?? [], correspondent: correspondents.data ?? [],
          document_type: types.data ?? [], storage_path: paths.data ?? [] }}
        onChange={(filters) => setDraft({ ...draft, definition: { version: 1, filters } })} />}
      <div className="flex gap-2">
        <Button type="submit" disabled={busy || !draft.name.trim() || validation.data?.compilable !== true}>{m.librarySave}</Button>
        <Button type="button" variant="ghost" disabled={busy} onClick={() => open()}>{m.libraryCancel}</Button>
      </div>
      {(error || validation.isError) && <p role="alert" className="text-destructive">{error || errorMessage(validation.error)}</p>}
    </form>
  </div>
}
