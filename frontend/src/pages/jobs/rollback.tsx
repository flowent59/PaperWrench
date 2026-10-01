import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router'

import { ApiError, jobsApi, previewsApi } from '@/api/client'
import type { CreatedPreview } from '@/api/types'
import { Button } from '@/components/ui/button'
import { messages } from '@/i18n/messages'
import { formatDateTime, formatNumber } from '@/i18n/format'
import { errorMessage, issueMessage } from '@/i18n/errors'
import { valueText } from '@/pages/transformations/value'

const m = messages.rollback
const p = messages.preview

export function RollbackReview({ jobId, blocked = false }: { jobId: number; blocked?: boolean }) {
  const [scope, setScope] = useState<'full' | 'selected'>('full')
  const [selection, setSelection] = useState<Set<number>>(() => new Set())
  const [candidatePage, setCandidatePage] = useState(1)
  const [search, setSearch] = useState('')
  const [candidateState, setCandidateState] = useState('all')
  const [preview, setPreview] = useState<CreatedPreview | null>(null)
  const [page, setPage] = useState(1)
  const [busy, setBusy] = useState(false)
  const [ack, setAck] = useState(false)
  const [race, setRace] = useState(false)
  const [error, setError] = useState('')
  const [expired, setExpired] = useState(false)
  const [uncertain, setUncertain] = useState(false)
  const [created, setCreated] = useState<number | null>(null)
  const alive = useRef(true)
  const retained = useRef<string | null>(null)
  const rows = useQuery({ queryKey: ['rollback-preview', preview?.id, page],
    queryFn: () => previewsApi.page(preview!.id, page, ''), enabled: !!preview && !expired,
    retry: false, gcTime: 0 })
  const candidates = useQuery({
    queryKey: ['rollback-candidates', jobId, candidatePage, search, candidateState, created],
    queryFn: () => jobsApi.rollbackCandidates(jobId, candidatePage, search, candidateState),
    enabled: scope === 'selected', retry: false, refetchInterval: 3000,
  })
  const createdJob = useQuery({
    queryKey: ['job', created], queryFn: () => jobsApi.detail(created!),
    enabled: created !== null, retry: false,
    refetchInterval: (query) => ['completed', 'partial', 'failed', 'cancelled'].includes(
      query.state.data?.status ?? 'pending',
    ) ? false : 2000,
  })
  const createdRunning = created !== null && !['completed', 'partial', 'failed', 'cancelled'].includes(
    createdJob.data?.status ?? 'pending',
  )

  function invalidatePreview() {
    if (retained.current) void previewsApi.discard(retained.current).catch(() => {})
    retained.current = null
    setPreview(null); setAck(false); setRace(false); setExpired(false)
  }

  function toggle(documentId: number) {
    invalidatePreview()
    setSelection(current => {
      const next = new Set(current)
      if (next.has(documentId)) next.delete(documentId)
      else if (next.size < 500) next.add(documentId)
      return next
    })
  }

  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
      if (retained.current) void previewsApi.discard(retained.current).catch(() => {})
    }
  }, [])
  useEffect(() => {
    if (!preview) return
    const timer = window.setTimeout(() => setExpired(true), Math.max(0, Date.parse(preview.expires_at) - Date.now()))
    return () => window.clearTimeout(timer)
  }, [preview])

  async function build() {
    setBusy(true); setError(''); setAck(false); setRace(false); setExpired(false); setCreated(null)
    try {
      if (retained.current) await previewsApi.discard(retained.current)
      retained.current = null
      setPreview(null)
      const result = await jobsApi.rollbackPreview(
        jobId, scope === 'selected' ? [...selection].sort((a, b) => a - b) : undefined,
      )
      if (!alive.current) { await previewsApi.discard(result.id); return }
      retained.current = result.id
      setPreview(result); setPage(1)
    } catch (cause) { if (alive.current) setError(errorMessage(cause, p.failure)) }
    finally { if (alive.current) setBusy(false) }
  }
  async function confirm() {
    if (!preview) return
    setBusy(true); setError('')
    try {
      const result = await jobsApi.rollback(jobId, preview, race)
      if (alive.current) { setCreated(result.id); setPreview({ ...preview, preview_token: '', confirmed: true }) }
    } catch (cause) {
      if (alive.current) {
        setError(errorMessage(cause, p.failure))
        if (cause instanceof ApiError && cause.code === 'PREVIEW_STALE') setExpired(true)
        if (!(cause instanceof ApiError) || cause.status >= 500) setUncertain(true)
      }
    } finally { if (alive.current) setBusy(false) }
  }
  return <section className="space-y-3 rounded border p-4" aria-label={m.title}>
    <h2 className="font-semibold">{m.title}</h2><p>{m.description}</p>
    <fieldset className="space-y-2" disabled={busy}>
      <legend className="font-medium">{m.scope}</legend>
      <label className="mr-4 inline-flex gap-2"><input type="radio" name={`rollback-scope-${jobId}`}
        checked={scope === 'full'} onChange={() => { invalidatePreview(); setScope('full') }} />{m.full}</label>
      <label className="inline-flex gap-2"><input type="radio" name={`rollback-scope-${jobId}`}
        checked={scope === 'selected'} onChange={() => { invalidatePreview(); setScope('selected') }} />{m.selected}</label>
    </fieldset>
    {scope === 'selected' && <div className="space-y-2 rounded border p-3">
      <p className="text-sm">{m.selectiveHelp}</p>
      <div className="flex flex-wrap gap-3">
        <label>{m.search}<input className="ml-2 rounded border p-1" value={search}
          onChange={e => { setSearch(e.target.value); setCandidatePage(1) }} /></label>
        <label>{m.filter}<select className="ml-2 rounded border p-1" value={candidateState}
          onChange={e => { setCandidateState(e.target.value); setCandidatePage(1) }}>
          {Object.entries(m.states).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select></label>
      </div>
      {candidates.isError && <p role="alert">{errorMessage(candidates.error)}</p>}
      {candidates.isLoading && <p role="status">{messages.jobs.loading}</p>}
      <p role="status">{m.selectedCount.replace('{count}', formatNumber(selection.size))}</p>
      <div className="flex flex-wrap gap-2">
        <Button variant="outline" disabled={busy || !candidates.data?.items.some(item =>
          item.status === 'available' && !selection.has(item.document_id))}
          onClick={() => { invalidatePreview(); setSelection(current => {
            const next = new Set(current)
            for (const item of candidates.data?.items ?? []) {
              if (item.status === 'available' && next.size < 500) next.add(item.document_id)
            }
            return next
          }) }}>{m.selectPage}</Button>
        <Button variant="outline" disabled={busy || selection.size === 0}
          onClick={() => { invalidatePreview(); setSelection(new Set()) }}>{m.clearSelection}</Button>
      </div>
      <ul className="space-y-1">{candidates.data?.items.map(item => <li key={item.document_id}>
        <label className="flex items-center gap-2"><input type="checkbox" checked={selection.has(item.document_id)}
          disabled={busy || item.status !== 'available' || (!selection.has(item.document_id) && selection.size >= 500)}
          onChange={() => toggle(item.document_id)} />{item.title ?? p.unavailable} (#{item.document_id})
          {' · '}{m.states[item.status]}</label>
      </li>)}</ul>
      <div className="flex items-center gap-2">
        <Button variant="outline" disabled={candidatePage <= 1 || candidates.isFetching}
          onClick={() => setCandidatePage(candidatePage - 1)}>{p.previous}</Button>
        <span>{p.page} {candidatePage} / {Math.max(1, candidates.data?.page_count ?? 1)}</span>
        <Button variant="outline" disabled={candidatePage >= (candidates.data?.page_count ?? 1) || candidates.isFetching}
          onClick={() => setCandidatePage(candidatePage + 1)}>{p.next}</Button>
      </div>
    </div>}
    {(blocked || createdRunning) && <p role="status">{m.waitForPrevious}</p>}
    <Button disabled={busy || uncertain || blocked || createdRunning ||
      (scope === 'selected' && selection.size === 0)}
      onClick={build}>{created !== null ? m.new : m.preview}</Button>
    {error && <p role="alert">{error}</p>}
    {rows.error && <p role="alert">{errorMessage(rows.error)}</p>}
    {uncertain && <p role="alert">{m.uncertain} <Link to="/history">{messages.jobs.open}</Link></p>}
    {created !== null && <Link className="text-primary underline" to={`/jobs/${created}`}>{m.linked} #{created}</Link>}
    {preview && <>
      <p>{p.changed}: {formatNumber(preview.changed)} · {p.unchanged}: {formatNumber(preview.unchanged)} · {p.errors}: {formatNumber(preview.errors)}</p>
      <p>{p.expiresAt} {formatDateTime(preview.expires_at)}</p>
      {!preview.changed && <p role="status">{m.none}</p>}
      {expired && <p role="alert">{p.expired}</p>}
      {rows.data?.items.map(row => <article className="space-y-2 border-t pt-2" key={row.document_id}>
        <h3>{row.title} (#{row.document_id}) · {p.statuses[row.status]}</h3>
        {row.issue && <p>{issueMessage(row.issue)}</p>}
        {row.changes.map((change, index) => <div key={index} className="rounded border p-2">
          <p>{change.field.source === 'core' ? change.field.name : `#${change.field.field_id}`}: {' '}
            {valueText(change.before)} → {valueText(change.intended)}</p>
          <dl className="grid gap-2 sm:grid-cols-3">
            <div><dt>{m.before}</dt><dd>{valueText(change.intended)}</dd></div>
            <div><dt>{m.current}</dt><dd>{valueText(change.before)}</dd></div>
            <div><dt>{m.restored}</dt><dd>{valueText(change.intended)}</dd></div>
          </dl>
          {change.issue && <p>{issueMessage(change.issue)}</p>}
        </div>)}
        {Object.entries(row.excluded_operations ?? {}).map(([field, reason]) => <p key={field}>{field}: {issueMessage(reason)}</p>)}
      </article>)}
      <div className="flex items-center gap-3">
        <Button variant="outline" disabled={rows.isFetching || page <= 1} onClick={() => setPage(page - 1)}>{p.previous}</Button>
        <span>{p.page} {page} / {Math.max(1, rows.data?.page_count ?? 1)}</span>
        <Button variant="outline" disabled={rows.isFetching || page >= (rows.data?.page_count ?? 1)} onClick={() => setPage(page + 1)}>{p.next}</Button>
      </div>
      <label className="flex gap-2"><input type="checkbox" checked={ack} onChange={e => setAck(e.target.checked)} />{m.acknowledge}</label>
      {preview.requires_external_race_ack && <>
        <p>{messages.jobs.race}</p>
        <label className="flex gap-2"><input type="checkbox" checked={race} onChange={e => setRace(e.target.checked)} />{messages.jobs.acknowledgeRace}</label>
      </>}
      <Button onClick={confirm} disabled={busy || !ack || !preview.changed || expired || uncertain || preview.confirmed || rows.isError || !rows.data || (preview.requires_external_race_ack && !race)}>{m.confirm}</Button>
    </>}
  </section>
}
