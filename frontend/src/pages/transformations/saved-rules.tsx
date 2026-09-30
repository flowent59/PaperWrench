import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'

import { ApiError, collectionsApi, NetworkError, rulesApi } from '@/api/client'
import type { SavedRule, SavedRuleDefinition, Transformation } from '@/api/types'
import { Button } from '@/components/ui/button'
import { errorMessage } from '@/i18n/errors'
import { messages } from '@/i18n/messages'

import { DryRun } from './dry-run'

const input = 'h-9 rounded-md border border-input bg-background px-2 text-sm'

interface RuleAction {
  (): Promise<SavedRule>
}

export function SavedRules({ build }: { build: (forCollection: boolean) => Transformation }) {
  const m = messages.rules
  const cache = useQueryClient()
  const rules = useQuery({ queryKey: ['saved-rules'], queryFn: rulesApi.list })
  const collections = useQuery({ queryKey: ['collections'], queryFn: collectionsApi.list })
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [target, setTarget] = useState<'filter' | 'collection'>('filter')
  const [collectionId, setCollectionId] = useState('')
  const [selected, setSelected] = useState<SavedRule | null>(null)
  const [editingName, setEditingName] = useState('')
  const [editingDescription, setEditingDescription] = useState('')
  const [history, setHistory] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const revisions = useQuery({
    queryKey: ['rule-revisions', selected?.id],
    queryFn: () => rulesApi.revisions(selected!.id),
    enabled: selected !== null && history,
  })

  function definition(): SavedRuleDefinition {
    const transformation = build(target === 'collection')
    if (target === 'collection') {
      const id = Number(collectionId)
      if (!Number.isSafeInteger(id) || id < 1) throw new Error(m.chooseCollection)
      return { target: { kind: 'collection', collection_id: id }, operations: transformation.operations }
    }
    if (transformation.targets.source !== 'dataset') throw new Error(m.datasetRequired)
    const query = transformation.targets.query
    if (!query.search && !query.filters?.root.children.length) throw new Error(m.filterRequired)
    return { target: { kind: 'filter', query }, operations: transformation.operations }
  }

  async function run(action: RuleAction) {
    setBusy(true)
    setError('')
    try {
      const result = await action()
      await cache.invalidateQueries({ queryKey: ['saved-rules'] })
      setSelected(result)
      setEditingName(result.name)
      setEditingDescription(result.description ?? '')
      setName('')
    } catch (cause) {
      setError(cause instanceof Error && !(cause instanceof ApiError) && !(cause instanceof NetworkError)
        ? cause.message : errorMessage(cause, m.error))
    }
    finally { setBusy(false) }
  }

  async function remove() {
    if (!selected || !window.confirm(m.deleteConfirm)) return
    setBusy(true)
    setError('')
    try {
      await rulesApi.remove(selected.id)
      setSelected(null)
      await cache.invalidateQueries({ queryKey: ['saved-rules'] })
    } catch (cause) { setError(errorMessage(cause, m.error)) }
    finally { setBusy(false) }
  }

  return <section className="space-y-4 rounded-lg border p-4" aria-label={m.title}>
    <h2 className="text-lg font-semibold">{m.title}</h2>
    <p className="text-sm text-muted-foreground">{m.help}</p>
    <Schedules />
    <div className="flex flex-wrap items-end gap-2">
      <label className="text-sm">{m.name}<input className={`${input} ml-2`} value={name}
        onChange={(event) => setName(event.target.value)} /></label>
      <label className="text-sm">{m.description}<input className={`${input} ml-2`} value={description}
        onChange={(event) => setDescription(event.target.value)} /></label>
      <label className="text-sm">{m.target}<select className={`${input} ml-2`} value={target}
        onChange={(event) => setTarget(event.target.value as typeof target)}>
        <option value="filter">{m.currentFilter}</option><option value="collection">{m.collection}</option>
      </select></label>
      {target === 'collection' && <select aria-label={m.chooseCollection} className={input}
        value={collectionId} onChange={(event) => setCollectionId(event.target.value)}>
        <option value="">{m.chooseCollection}</option>
        {collections.data?.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
      </select>}
      <Button disabled={busy || !name.trim()} onClick={() => void run(() => rulesApi.create({
        name, description, definition: definition(),
      }))}>{m.save}</Button>
    </div>
    {error && <p role="alert" className="text-destructive">{error}</p>}
    {rules.isError && <p role="alert">{errorMessage(rules.error, m.error)}</p>}
    {rules.data?.length === 0 && <p className="text-sm text-muted-foreground">{m.empty}</p>}
    <div className="flex flex-wrap gap-2">{rules.data?.map((rule) =>
      <Button key={rule.id} variant="outline" onClick={() => {
        setSelected(rule); setEditingName(rule.name)
        setEditingDescription(rule.description ?? ''); setHistory(false)
      }}>{rule.name}</Button>)}</div>
    {selected && <div className="space-y-3 rounded-lg border p-3">
      <div className="flex flex-wrap items-center gap-2">
        <strong>{selected.name}</strong><span className="text-sm text-muted-foreground">{m.revision} {selected.revision}</span>
        <Button variant="outline" onClick={() => setHistory(!history)}>{m.history}</Button>
        <Button variant="outline" onClick={() => setSelected(null)}>{m.close}</Button>
      </div>
      <div className="flex flex-wrap items-end gap-2">
        <label className="text-sm">{m.name}<input className={`${input} ml-2`} value={editingName}
          onChange={(event) => setEditingName(event.target.value)} /></label>
        <label className="text-sm">{m.description}<input className={`${input} ml-2`} value={editingDescription}
          onChange={(event) => setEditingDescription(event.target.value)} /></label>
        <Button variant="outline" disabled={busy || !editingName.trim()}
          onClick={() => void run(() => rulesApi.update(selected.id, {
            name: editingName, description: editingDescription, definition: selected.definition,
            expected_revision: selected.revision,
          }))}>{m.rename}</Button>
        <Button variant="outline" disabled={busy}
          onClick={() => void run(() => rulesApi.update(selected.id, {
            name: editingName, description: editingDescription, definition: definition(),
            expected_revision: selected.revision,
          }))}>{m.replace}</Button>
        <Button variant="outline" disabled={busy} onClick={() => void remove()}>{m.delete}</Button>
      </div>
      {history && <ol className="list-inside list-decimal text-sm">{revisions.data?.map((item) =>
        <li key={item.revision}>{m.revision} {item.revision}: {item.name} ({item.created_at})</li>)}</ol>}
      <h3 className="font-medium">{m.run}</h3>
      <DryRun key={`${selected.id}-${selected.revision}`} ruleId={selected.id} />
    </div>}
  </section>
}
import { Schedules } from './schedules'
