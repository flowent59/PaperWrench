import { useQuery } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'

import { filterLibraryApi } from '@/api/client'
import type { FilterSet } from '@/api/types'
import { Button } from '@/components/ui/button'
import { errorMessage } from '@/i18n/errors'
import { messages } from '@/i18n/messages'

export const filterLibraryKey = ['filter-library'] as const

export function FilterLibraryPicker({ filters, onChange }: { filters: FilterSet; onChange: (filters: FilterSet) => void }) {
  const [enabled, setEnabled] = useState(false)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const entries = useQuery({ queryKey: filterLibraryKey, queryFn: filterLibraryApi.list, enabled })
  const m = messages.filters
  const active = useRef<AbortController | null>(null)
  useEffect(() => () => active.current?.abort(), [filters])

  async function insert(id: number) {
    const entry = entries.data?.find((item) => item.id === id)
    if (!entry) return
    const controller = new AbortController()
    active.current?.abort()
    active.current = controller
    setBusy(true)
    setError('')
    try {
      const definition = await filterLibraryApi.criteria(entry.id, controller.signal)
      if (!controller.signal.aborted) onChange(definition.filters)
    } catch (cause) {
      if (!controller.signal.aborted) setError(errorMessage(cause))
    } finally {
      if (active.current === controller) setBusy(false)
    }
  }

  return <div className="space-y-1">
    <Button type="button" size="sm" variant="outline" aria-expanded={enabled}
      onClick={() => setEnabled(!enabled)}>{m.useLibrary}</Button>
    {enabled && <div>
      <p className="text-sm">{m.copyLibrary}</p>
      {entries.isPending ? <p role="status">{m.counting}</p>
        : entries.isError ? <p role="alert">{errorMessage(entries.error)}</p>
        : <select className="rounded border bg-background p-2" aria-label={m.useLibrary}
          disabled={busy || entries.data.length === 0} value=""
          onChange={(event) => void insert(Number(event.target.value))}>
          <option value="">{entries.data.length ? m.chooseValue : m.libraryEmpty}</option>
          {entries.data.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select>}
    </div>}
    {error && <p role="alert" className="text-destructive">{error}</p>}
  </div>
}
