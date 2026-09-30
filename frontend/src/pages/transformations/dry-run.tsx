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
export function DryRun({ build, onPreviewCreated }: { build: () => Transformation; onPreviewCreated?: () => void }) {
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
  const [clearAcknowledged, setClearAcknowledged] = useState(false)
  const [jobId, setJobId] = useState<number | null>(null)
  const [uncertainApply, setUncertainApply] = useState(false)
  const customWrite = spec?.operations.some((operation) => operation.field.source === 'custom_field') ?? false
  const clearsValues = spec?.operations.some((operation) => operation.operation === 'clear') ?? false
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
  const statusClass = (value: 'change' | 'unchanged' | 'error') => value === 'error'
    ? 'border-destructive bg-destructive/10 text-destructive'
    : value === 'change' ? 'border-primary bg-primary/10' : 'border-muted bg-muted'

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
    setClearAcknowledged(false)
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
      onPreviewCreated?.()
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
      <p role="status" className="sr-only">{m.matched}: {formatNumber(preview.matched)}. {m.errors}: {formatNumber(preview.errors)}.</p>
      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        {(['matched', 'evaluated', 'changed', 'unchanged', 'errors'] as const).map((name) =>
          <div key={name} className={`rounded-md border p-3 ${name === 'errors' && preview.errors > 0 ? statusClass('error') : ''}`}>
            <dt className="text-sm text-muted-foreground">{m[name]}</dt>
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
          <div><table className="block w-full text-left text-sm sm:table">
            <thead className="sr-only sm:not-sr-only sm:table-header-group"><tr>{[m.document, m.fieldChanges, m.status].map((label) =>
              <th className="p-2" key={label}>{label}</th>)}</tr></thead>
            <tbody className="block sm:table-row-group">{rows.items.map((row) => <tr className="mb-3 block rounded-md border align-top sm:mb-0 sm:table-row sm:rounded-none sm:border-0 sm:border-t" key={row.document_id}>
              <td className="block p-2 sm:table-cell">{row.title ?? m.unavailable} (#{row.document_id})
                {row.issue && <p role="alert" className="text-destructive">{issueMessage(row.issue)}</p>}
                {row.excluded_operations && Object.entries(row.excluded_operations).map(([key, reason]) =>
                  <p key={key} role="status" className="rounded border border-amber-600 p-2 text-amber-800 dark:text-amber-200">
                    {m.warning}: {reason}
                  </p>)}
              </td>
              <td className="block p-2 sm:table-cell"><div className="space-y-3">
                {row.changes.map((change, index) => <div key={index} className={`rounded-md border p-2 ${statusClass(change.status)}`}>
                  <strong>{change.field.source === 'core' ? change.field.name
                    : `${change.field.display_name ?? m.unknownField} (#${change.field.field_id})`}</strong>
                  <dl className="mt-2 grid gap-2 sm:grid-cols-2">
                    <div><dt className="text-xs text-muted-foreground">{m.before}</dt>
                      <dd className="break-words whitespace-pre-wrap">{valueText(change.before)}</dd></div>
                    <div><dt className="text-xs text-muted-foreground">{m.intended}</dt>
                      <dd className="break-words whitespace-pre-wrap">{valueText(change.intended)}</dd></div>
                  </dl>
                  <p className="mt-2 text-xs">{m.statuses[change.status]}</p>
                  {change.issue && <p role="alert" className="mt-1">{issueMessage(change.issue)}</p>}
                </div>)}</div></td>
              <td className="block p-2 sm:table-cell"><span className={`inline-block rounded-md border px-2 py-1 ${statusClass(row.status)}`}>
                {m.statuses[row.status]}</span></td>
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
        {preview.changed > 0 && <p className="rounded-md border border-primary bg-primary/10 p-3 text-sm">
          {m.confirmScope.replace('{count}', formatNumber(preview.changed))}
        </p>}
        <label className="flex items-center gap-2 text-sm"><input type="checkbox"
          checked={acknowledged} disabled={preview.confirmed || preview.errors > 0 || !preview.changed}
          onChange={(event) => setAcknowledged(event.target.checked)} />{m.acknowledge}</label>
        {clearsValues && <label className="flex items-center gap-2 rounded-md border border-amber-600 p-3 text-sm"><input
          type="checkbox" checked={clearAcknowledged} disabled={preview.confirmed || busy}
          onChange={(event) => setClearAcknowledged(event.target.checked)} />{m.acknowledgeClear}</label>}
        {customWrite && <div className="space-y-2 text-sm">
          <p>{messages.jobs.race}</p>
          <label className="flex items-center gap-2"><input type="checkbox" checked={raceAcknowledged}
            disabled={preview.confirmed || busy}
            onChange={(event) => setRaceAcknowledged(event.target.checked)} />{messages.jobs.acknowledgeRace}</label>
        </div>}
        <Button variant="outline" onClick={confirm} disabled={busy || loading || !rows || !acknowledged ||
          (clearsValues && !clearAcknowledged) ||
          uncertainApply || (customWrite && !raceAcknowledged) || preview.confirmed || preview.errors > 0 || !preview.changed}>{m.confirm}</Button>
        {preview.confirmed && <p role="status">{m.confirmed}</p>}
      </>}
    </>}
    {jobId !== null && <a className="text-primary underline" href={`/jobs/${jobId}`}>{messages.jobs.open}</a>}
    {uncertainApply && <div role="alert"><p>{messages.jobs.uncertainApply}</p>
      <a className="text-primary underline" href="/history">{messages.jobs.open}</a></div>}
  </section>
}
