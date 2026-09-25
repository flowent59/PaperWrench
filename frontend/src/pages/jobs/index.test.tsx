import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { JobView, TargetView } from '@/api/types'
import { messages } from '@/i18n/messages'

import { HistoryPageView, JobPage } from '.'

const m = messages.jobs
const job: JobView = { id: 1, title: 'Document transformation', status: 'partial', total: 2, processed: 2,
  counts: { pending: 0, reading: 0, writing: 0, succeeded: 1, unchanged: 0, conflict: 0, permission: 0,
    missing: 0, failed: 0, ambiguous: 1 }, source_kind: 'ids', dataset_query: null, operations: [],
  preview: null, created_at: '2026-09-24T10:00:00Z', started_at: '2026-09-24T10:00:01Z',
  finished_at: '2026-09-24T10:00:02Z', resumable: false }
const target: TargetView = { document_id: 7, position: 0, title: 'Vacations', status: 'ambiguous',
  error: 'PROCESS_INTERRUPTED', http_status: null, attempts: 1, started_at: job.started_at, finished_at: job.finished_at }
const envelope = <T,>(items: T[], total = items.length) => ({ items, total, page: 1, page_size: 25, page_count: Math.ceil(total / 25) })

function setup(path = '/jobs/1', value = job) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url.includes('/operations?')) return new Response(JSON.stringify(envelope([{
      id: 1, document_id: 7, field_kind: 'core', field_key: 'core:title', status: 'ambiguous',
      before: { kind: 'present', raw: 'Before' }, intended: { kind: 'present', raw: 'Intended' },
      written: null, rollback_candidate: false, error: 'PROCESS_INTERRUPTED',
    }])))
    if (url.includes('/targets?')) return new Response(JSON.stringify(envelope([target], 100)))
    if (url.includes('/jobs?')) return new Response(JSON.stringify(envelope([value], 100)))
    if (url.endsWith('/resume') && init?.method === 'POST') value = { ...value, status: 'running', resumable: false }
    return new Response(JSON.stringify(value))
  })
  vi.stubGlobal('fetch', fetchMock)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const view = render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[path]}><Routes>
    <Route path="/history" element={<HistoryPageView />} /><Route path="/jobs/:jobId" element={<JobPage />} />
  </Routes></MemoryRouter></QueryClientProvider>)
  return { fetchMock, view }
}

afterEach(() => vi.unstubAllGlobals())

describe('durable History', () => {
  it('shows partial and ambiguous outcomes, actual operation evidence and no restore action', async () => {
    setup()
    await screen.findByText(m.ambiguous)
    expect(screen.getByRole('progressbar', { name: m.progress })).toHaveAttribute('value', '2')
    fireEvent.click(await screen.findByRole('button', { name: m.inspect }))
    const details = await screen.findByRole('region', { name: m.operations })
    await within(details).findByText('Before', { selector: 'dd' })
    expect(within(details).getByText('Intended', { selector: 'dd' })).toBeInTheDocument()
    expect(within(details).getByText(m.noProvenance)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /rollback|restore/i })).not.toBeInTheDocument()
  })

  it('loads server target pages and status filters', async () => {
    const { fetchMock } = setup()
    await waitFor(() => expect(screen.getByRole('button', { name: m.next })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: m.next }))
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).includes('page=2&page_size=25'))).toBe(true))
    fireEvent.change(screen.getByLabelText(m.show), { target: { value: 'ambiguous' } })
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith('page=1&page_size=25&status=ambiguous'))).toBe(true))
  })

  it('refetches durable state after remount and resumes only on explicit action', async () => {
    const first = setup('/jobs/1', { ...job, status: 'interrupted', resumable: true, processed: 1 })
    await screen.findByText(m.interrupted)
    expect(first.fetchMock.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false)
    first.view.unmount()
    const second = setup('/jobs/1', { ...job, status: 'interrupted', resumable: true, processed: 1 })
    fireEvent.click(await screen.findByRole('button', { name: m.resume }))
    await screen.findByText(/RUNNING/)
    expect(second.fetchMock.mock.calls.filter(([url]) => String(url).endsWith('/resume'))).toHaveLength(1)
  })

  it('paginates History without loading all Jobs', async () => {
    const { fetchMock } = setup('/history')
    await screen.findByRole('link', { name: 'Document transformation #1' })
    fireEvent.click(screen.getByRole('button', { name: m.next }))
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith('/jobs?page=2&page_size=25'))).toBe(true))
  })
})
