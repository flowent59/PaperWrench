import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'

import { jobsApi } from '@/api/client'
import type { HistoryPage, JobView, OperationView, TargetStatus } from '@/api/types'
import { Button } from '@/components/ui/button'
import { messages } from '@/i18n/messages'
import { valueText } from '@/pages/transformations/value'

import { RollbackReview } from './rollback'

const m = messages.jobs
const active = (job?: JobView) => job?.status === 'pending' || job?.status === 'running'

function Pagination({ data, page, setPage, busy }: {
  data: Pick<HistoryPage<unknown>, 'page_count'> | undefined; page: number; setPage: (page: number) => void; busy: boolean
}) {
  return <div className="flex items-center gap-3">
    <Button variant="outline" disabled={busy || page <= 1} onClick={() => setPage(page - 1)}>{m.previous}</Button>
    <span>{m.page} {page} / {Math.max(data?.page_count ?? 1, 1)}</span>
    <Button variant="outline" disabled={busy || page >= (data?.page_count ?? 1)}
      onClick={() => setPage(page + 1)}>{m.next}</Button>
  </div>
}

export function HistoryPageView() {
  const [page, setPage] = useState(1)
  const query = useQuery({ queryKey: ['jobs', page], queryFn: () => jobsApi.list(page),
    refetchInterval: 3000 })
  return <section className="space-y-5">
    <h1 className="text-2xl font-semibold">{m.title}</h1>
    {query.isLoading && <p role="status">{m.loading}</p>}
    {query.error && <p role="alert">{query.error.message}</p>}
    {query.data?.items.length === 0 && <p>{m.empty}</p>}
    <div className="overflow-x-auto"><table className="w-full text-left text-sm">
      <thead><tr>{[m.detail, m.status, m.progress, m.created].map((label) =>
        <th className="p-3" key={label}>{label}</th>)}</tr></thead>
      <tbody>{query.data?.items.map((job) => <tr key={job.id} className="border-t">
        <td className="p-3"><Link className="text-primary underline" to={`/jobs/${job.id}`}>{job.title} #{job.id}</Link></td>
        <td className="p-3">{job.status.toUpperCase()}</td>
        <td className="p-3">{job.processed} / {job.total}</td>
        <td className="p-3">{new Date(job.created_at).toLocaleString()}</td>
      </tr>)}</tbody>
    </table></div>
    <Pagination data={query.data} page={page} setPage={setPage} busy={query.isFetching} />
  </section>
}

function OperationDetails({ jobId, documentId, polling, revision }: { jobId: number; documentId: number; polling: boolean; revision: string }) {
  const [page, setPage] = useState(1)
  const query = useQuery({ queryKey: ['job-operations', jobId, documentId, page, revision],
    queryFn: () => jobsApi.operations(jobId, documentId, page), refetchInterval: polling ? 2000 : false })
  return <section className="space-y-3 rounded border p-4" aria-label={m.operations}>
    <h2 className="font-semibold">{m.operations} · #{documentId}</h2>
    {query.error && <p role="alert">{query.error.message}</p>}
    {query.isLoading && <p role="status">{m.loading}</p>}
    {query.data?.items.map((operation: OperationView) => <article className="space-y-2 border-t pt-3" key={operation.id}>
      <h3 className="font-medium">{operation.field_key} · {operation.status.toUpperCase()}</h3>
      {operation.rollback_of_operation_id != null && <p>{messages.rollback.operation} #{operation.rollback_of_operation_id}</p>}
      <dl className="grid gap-3 sm:grid-cols-3">{(['before', 'intended', 'written'] as const).map((key) =>
        <div key={key}><dt className="text-muted-foreground">{m[key]}</dt>
          <dd className="whitespace-pre-wrap break-words">{valueText(operation[key])}</dd></div>)}</dl>
      <p className="text-sm text-muted-foreground">{operation.rollback_candidate ? m.provenance : m.noProvenance}</p>
      {operation.error && <p role="alert">{operation.error} {operation.http_status ?? ''}</p>}
    </article>)}
    <Pagination data={query.data} page={page} setPage={setPage} busy={query.isFetching} />
  </section>
}

export function JobPage() {
  const { jobId: rawId } = useParams()
  const jobId = Number(rawId)
  const [page, setPage] = useState(1)
  const [status, setStatus] = useState('')
  const [documentId, setDocumentId] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const query = useQuery({ queryKey: ['job', jobId], queryFn: () => jobsApi.detail(jobId),
    refetchInterval: (query) => active(query.state.data) ? 2000 : false })
  const job = query.data
  const targets = useQuery({ queryKey: ['job-targets', jobId, page, status, job?.processed],
    queryFn: () => jobsApi.targets(jobId, page, status), enabled: !!job,
    refetchInterval: active(job) ? 2000 : false })

  async function resume() {
    setBusy(true)
    setError('')
    try {
      await jobsApi.resume(jobId)
      await query.refetch()
      await targets.refetch()
    } catch (cause) { setError(cause instanceof Error ? cause.message : messages.errors.generic) }
    finally { setBusy(false) }
  }

  return <section className="space-y-5">
    <Link className="text-primary underline" to="/history">{m.title}</Link>
    <h1 className="text-2xl font-semibold">{m.detail} #{jobId}</h1>
    {query.isLoading && <p role="status">{m.loading}</p>}
    {query.error && <p role="alert">{query.error.message}</p>}
    {error && <p role="alert">{error}</p>}
    {job && <>
      <p className="font-medium">{job.status.toUpperCase()} · {job.processed} / {job.total}</p>
      <progress className="h-3 w-full" aria-label={m.progress} value={job.processed} max={Math.max(1, job.total)} />
      <dl className="flex flex-wrap gap-5">{Object.entries(job.counts).map(([status, count]) =>
        <div key={status}><dt className="text-sm text-muted-foreground">{m.outcomes[status as TargetStatus]}</dt>
          <dd className="text-xl">{count}</dd></div>)}</dl>
      <dl className="flex flex-wrap gap-5 text-sm">{(['created', 'started', 'finished'] as const).map((key) =>
        <div key={key}><dt>{m[key]}</dt><dd>{job[`${key}_at`] ? new Date(job[`${key}_at`]!).toLocaleString() : '—'}</dd></div>)}</dl>
      {job.status === 'interrupted' && <p role="status">{m.interrupted}</p>}
      {job.counts.ambiguous > 0 && <p role="alert" className="rounded border border-amber-500 p-3">{m.ambiguous}</p>}
      {job.resumable && <Button onClick={resume} disabled={busy}>{busy ? m.resuming : m.resume}</Button>}
      {job.rollback_of_job_id != null && <Link className="text-primary underline" to={`/jobs/${job.rollback_of_job_id}`}>{messages.rollback.original} #{job.rollback_of_job_id}</Link>}
      {job.rollback_job_id != null && <Link className="text-primary underline" to={`/jobs/${job.rollback_job_id}`}>{messages.rollback.linked} #{job.rollback_job_id}</Link>}
      {job.type === 'transform' && job.rollback_job_id == null && ['completed', 'partial', 'failed', 'cancelled'].includes(job.status) && <RollbackReview key={job.id} jobId={job.id} />}
      <details><summary>{m.transformation}</summary>
        <pre className="overflow-x-auto rounded bg-muted p-3 text-xs">{JSON.stringify(job.operations, null, 2)}</pre></details>
      <details><summary>{m.source}</summary>{job.source_kind === 'ids' ? <p>{m.explicit}</p>
        : <pre className="overflow-x-auto rounded bg-muted p-3 text-xs">{JSON.stringify(job.dataset_query, null, 2)}</pre>}</details>
      <h2 className="font-semibold">{m.targets}</h2>
      <label>{m.show}<select className="ml-3 rounded border p-2" value={status}
        onChange={(event) => { setStatus(event.target.value); setPage(1); setDocumentId(null) }}>
        <option value="">{m.all}</option>
        {Object.entries(m.outcomes).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
      </select></label>
      {targets.error && <p role="alert">{targets.error.message}</p>}
      <div className="overflow-x-auto"><table className="w-full text-left text-sm"><tbody>
        {targets.data?.items.map((target) => <tr className="border-t" key={target.document_id}>
          <td className="p-3">{target.title} (#{target.document_id})</td>
          <td className="p-3">{m.outcomes[target.status]}{target.error && <p>{target.error} {target.http_status ?? ''}</p>}
            {Object.entries(target.excluded_operations ?? {}).map(([field, reason]) => <p key={field}>{field}: {reason}</p>)}
          </td>
          <td className="p-3"><Button variant="outline" onClick={() => setDocumentId(target.document_id)}>{m.inspect}</Button></td>
        </tr>)}
      </tbody></table></div>
      <Pagination data={targets.data} page={page} setPage={setPage} busy={targets.isFetching} />
      {documentId !== null && <OperationDetails key={documentId} jobId={jobId} documentId={documentId}
        polling={active(job)} revision={`${job.status}:${job.processed}`} />}
      <p className="text-sm text-muted-foreground">{m.noRollback}</p>
    </>}
  </section>
}
