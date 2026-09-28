import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router'

import { ApiError, jobsApi, previewsApi } from '@/api/client'
import type { CreatedPreview } from '@/api/types'
import { Button } from '@/components/ui/button'
import { messages } from '@/i18n/messages'
import { formatDateTime, formatNumber } from '@/i18n/format'
import { valueText } from '@/pages/transformations/value'

const m = messages.rollback
const p = messages.preview

export function RollbackReview({ jobId }: { jobId: number }) {
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
    setBusy(true); setError(''); setAck(false); setRace(false); setExpired(false)
    try {
      if (retained.current) await previewsApi.discard(retained.current)
      retained.current = null
      setPreview(null)
      const result = await jobsApi.rollbackPreview(jobId)
      if (!alive.current) { await previewsApi.discard(result.id); return }
      retained.current = result.id
      setPreview(result); setPage(1)
    } catch (cause) { if (alive.current) setError(cause instanceof Error ? cause.message : p.failure) }
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
        setError(cause instanceof Error ? cause.message : p.failure)
        if (cause instanceof ApiError && cause.code === 'PREVIEW_STALE') setExpired(true)
        if (!(cause instanceof ApiError) || cause.status >= 500) setUncertain(true)
      }
    } finally { if (alive.current) setBusy(false) }
  }
  return <section className="space-y-3 rounded border p-4" aria-label={m.title}>
    <h2 className="font-semibold">{m.title}</h2><p>{m.description}</p>
    <Button disabled={busy || created !== null || uncertain} onClick={build}>{m.preview}</Button>
    {error && <p role="alert">{error}</p>}
    {rows.error && <p role="alert">{rows.error.message}</p>}
    {uncertain && <p role="alert">{m.uncertain} <Link to="/history">{messages.jobs.open}</Link></p>}
    {created !== null && <Link className="text-primary underline" to={`/jobs/${created}`}>{m.linked} #{created}</Link>}
    {preview && <>
      <p>{p.changed}: {formatNumber(preview.changed)} · {p.unchanged}: {formatNumber(preview.unchanged)} · {p.errors}: {formatNumber(preview.errors)}</p>
      <p>{p.expiresAt} {formatDateTime(preview.expires_at)}</p>
      {!preview.changed && <p role="status">{m.none}</p>}
      {expired && <p role="alert">{p.expired}</p>}
      {rows.data?.items.map(row => <article className="space-y-2 border-t pt-2" key={row.document_id}>
        <h3>{row.title} (#{row.document_id}) · {p.statuses[row.status]}</h3>
        {row.issue && <p>{row.issue.code}: {row.issue.message}</p>}
        {row.changes.map((change, index) => <p key={index}>
          {change.field.source === 'core' ? change.field.name : `#${change.field.field_id}`}: {' '}
          {valueText(change.before)} → {valueText(change.intended)}
          {change.issue && ` · ${change.issue.code}: ${change.issue.message}`}
        </p>)}
        {Object.entries(row.excluded_operations ?? {}).map(([field, reason]) => <p key={field}>{field}: {reason}</p>)}
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
