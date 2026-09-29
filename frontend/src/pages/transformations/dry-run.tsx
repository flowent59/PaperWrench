import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'

import { ApiError, jobsApi, previewsApi } from '@/api/client'
import type { CreatedPreview, Transformation } from '@/api/types'
import { Button } from '@/components/ui/button'
import { messages } from '@/i18n/messages'
import { formatDateTime, formatNumber } from '@/i18n/format'
import { errorMessage, issueMessage } from '@/i18n/errors'

import { valueText } from './value'

/** The parent remounts this panel on any spec edit, invalidating the old confirmation. */
export function DryRun({ build }: { build: () => Transformation }) {
  const m = messages.preview
  const [preview, setPreview] = useState<CreatedPreview | null>(null)
  const [spec, setSpec] = useState<Transformation | null>(null)
  const [page, setPage] = useState(1)
  const [status, setStatus] = useState('')
  const [busy, setBusy] = useState(false)
  const [acknowledged, setAcknowledged] = useState(false)
  const [expired, setExpired] = useState(false)
  const [error, setError] = useState('')
  const [raceAcknowledged, setRaceAcknowledged] = useState(false)
  const [jobId, setJobId] = useState<number | null>(null)
  const [uncertainApply, setUncertainApply] = useState(false)
  const customWrite = spec?.operations.some((operation) => operation.field.source === 'custom_field') ?? false
  const alive = useRef(true)
  const retainedId = useRef<string | null>(null)
  const pageQuery = useQuery({
    queryKey: ['preview', preview?.id, page, status],
    queryFn: () => previewsApi.page(preview!.id, page, status),
    enabled: preview !== null && !expired,
    retry: false, gcTime: 0,
  })
  const rows = pageQuery.data
  const loading = pageQuery.isFetching
  const stale = expired || (pageQuery.error instanceof ApiError && pageQuery.error.code === 'PREVIEW_STALE')

  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
      if (retainedId.current) void previewsApi.discard(retainedId.current).catch(() => {})
    }
  }, [])

  useEffect(() => {
    if (!preview) return
    const timer = window.setTimeout(() => setExpired(true),
      Math.max(0, Date.parse(preview.expires_at) - Date.now()))
    return () => window.clearTimeout(timer)
  }, [preview])

  async function create() {
    setBusy(true)
    setError('')
    setAcknowledged(false)
    setRaceAcknowledged(false)
    setJobId(null)
    setUncertainApply(false)
    setExpired(false)
    try {
      const transformation = build()
      if (retainedId.current) await previewsApi.discard(retainedId.current)
      retainedId.current = null
      setPreview(null)
      const result = await previewsApi.create(transformation)
      if (!alive.current) {
        await previewsApi.discard(result.id)
        return
      }
      retainedId.current = result.id
      setSpec(transformation)
      setPage(1)
      setStatus('')
      setPreview(result)
    } catch (cause) {
      if (alive.current) setError(errorMessage(cause, m.failure))
    } finally { if (alive.current) setBusy(false) }
  }

  async function confirm() {
    if (!preview || !spec) return
    setBusy(true)
    setError('')
    try {
      const result = await jobsApi.create(preview, spec, raceAcknowledged)
      if (alive.current) {
        setPreview({ ...preview, confirmed: true, preview_token: '' })
        setJobId(result.id)
      }
    } catch (cause) {
      if (alive.current) {
        setError(errorMessage(cause, m.failure))
        if (cause instanceof ApiError && cause.code === 'PREVIEW_STALE') setExpired(true)
        if (!(cause instanceof ApiError) || cause.status >= 500) setUncertainApply(true)
      }
    } finally { if (alive.current) setBusy(false) }
  }

  return <section className="space-y-4 rounded-lg border p-4" aria-label={m.title}>
    <h2 className="font-semibold">{m.title}</h2>
    <p className="text-sm text-muted-foreground">{m.description}</p>
    <p className="text-sm text-muted-foreground">{m.limits}</p>
    <Button disabled={busy} onClick={create}>{busy ? m.building : m.create}</Button>
    {error && <p role="alert" className="text-destructive">{error}</p>}
    {pageQuery.isError && !stale && <p role="alert">{errorMessage(pageQuery.error)}</p>}
    {preview && <>
      <dl className="flex flex-wrap gap-6">
        {(['matched', 'evaluated', 'changed', 'unchanged', 'errors'] as const).map((name) =>
          <div key={name}><dt className="text-sm text-muted-foreground">{m[name]}</dt>
            <dd className="text-xl font-semibold">{formatNumber(preview[name])}</dd></div>)}
      </dl>
      <p className="text-sm">{m.observedAt} {formatDateTime(preview.created_at)}</p>
      <p className="text-sm">{m.expiresAt} {formatDateTime(preview.expires_at)}</p>
      <p className="text-sm text-muted-foreground">{m.staleness}</p>
      {stale ? <p role="alert">{m.expired}</p> : <>
        <label className="text-sm">{m.show}<select className="ml-2 rounded border p-1"
          value={status} onChange={(event) => { setStatus(event.target.value); setPage(1) }}>
          <option value="">{m.all}</option>
          <option value="change">{m.changed}</option><option value="unchanged">{m.unchanged}</option>
          <option value="error">{m.errors}</option>
        </select></label>
        {loading && <p role="status">{m.loading}</p>}
        {rows && <>
          <div className="overflow-x-auto"><table className="w-full text-left text-sm">
            <thead><tr>{[m.document, m.fieldChanges, m.status].map((label) =>
              <th className="p-2" key={label}>{label}</th>)}</tr></thead>
            <tbody>{rows.items.map((row) => <tr className="border-t align-top" key={row.document_id}>
              <td className="p-2">{row.title ?? m.unavailable} (#{row.document_id})
                {row.issue && <p className="text-destructive">{issueMessage(row.issue)}</p>}
              </td>
              <td className="p-2"><div className="space-y-2">
                {row.changes.map((change, index) => <div key={index}>
                  <strong>{change.field.source === 'core' ? change.field.name
                    : `${change.field.display_name ?? m.unknownField} (#${change.field.field_id})`}</strong>
                  <p className="whitespace-pre-wrap">{valueText(change.before)} → {valueText(change.intended)}</p>
                  <p>{m.statuses[change.status]}{change.issue &&
                    ` · ${issueMessage(change.issue)}`}</p>
                </div>)}</div></td>
              <td className="p-2">{m.statuses[row.status]}</td>
            </tr>)}</tbody>
          </table></div>
          <div className="flex items-center gap-3">
            <Button variant="outline" disabled={loading || page <= 1}
              onClick={() => setPage(page - 1)}>{m.previous}</Button>
            <span>{m.page} {page} / {Math.max(rows.page_count, 1)}</span>
            <Button variant="outline" disabled={loading || page >= rows.page_count}
              onClick={() => setPage(page + 1)}>{m.next}</Button>
          </div>
        </>}
        {preview.errors > 0 && <p role="alert">{m.fixErrors}</p>}
        <label className="flex items-center gap-2 text-sm"><input type="checkbox"
          checked={acknowledged} disabled={preview.confirmed || preview.errors > 0 || !preview.changed}
          onChange={(event) => setAcknowledged(event.target.checked)} />{m.acknowledge}</label>
        {customWrite && <div className="space-y-2 text-sm">
          <p>{messages.jobs.race}</p>
          <label className="flex items-center gap-2"><input type="checkbox" checked={raceAcknowledged}
            disabled={preview.confirmed || busy}
            onChange={(event) => setRaceAcknowledged(event.target.checked)} />{messages.jobs.acknowledgeRace}</label>
        </div>}
        <Button variant="outline" onClick={confirm} disabled={busy || loading || !rows || !acknowledged ||
          uncertainApply || (customWrite && !raceAcknowledged) || preview.confirmed || preview.errors > 0 || !preview.changed}>{m.confirm}</Button>
        {preview.confirmed && <p role="status">{m.confirmed}</p>}
      </>}
    </>}
    {jobId !== null && <a className="text-primary underline" href={`/jobs/${jobId}`}>{messages.jobs.open}</a>}
    {uncertainApply && <div role="alert"><p>{messages.jobs.uncertainApply}</p>
      <a className="text-primary underline" href="/history">{messages.jobs.open}</a></div>}
  </section>
}
