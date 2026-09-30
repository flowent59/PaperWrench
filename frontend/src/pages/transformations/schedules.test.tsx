import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import { messages } from '@/i18n/messages'
import { DryRun } from './dry-run'
import { Schedules } from './schedules'

afterEach(() => vi.unstubAllGlobals())

it('requires a reviewed preview and explicit unattended consent, including zero-change previews', async () => {
  const spec = { targets: { source: 'ids', document_ids: [1] },
    operations: [{ operation: 'set', field: { source: 'core', name: 'title' }, value: 'New' }] }
  const preview = { id: 'a'.repeat(32), preview_token: 'token', transformation: spec,
    version: 1, created_at: new Date().toISOString(), expires_at: new Date(Date.now() + 600_000).toISOString(),
    matched: 1, evaluated: 1, changed: 0, unchanged: 1, errors: 0,
    selection_fingerprint: 's', spec_fingerprint: 'p', target_fingerprint: 't',
    result_fingerprint: 'r', confirmed: false }
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url.endsWith('/preview')) return new Response(JSON.stringify(preview))
    if (url.includes('/documents')) return new Response(JSON.stringify({
      items: [], page: 1, page_count: 1, total: 1, page_size: 25,
    }))
    return new Response('{}')
  })
  vi.stubGlobal('fetch', fetchMock)
  const cache = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={cache}><DryRun ruleId={7} /></QueryClientProvider>)
  expect(screen.queryByText(messages.schedules.enable)).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: messages.preview.create }))
  const enable = await screen.findByRole('button', { name: messages.schedules.enable })
  expect(enable).toBeDisabled()
  fireEvent.click(screen.getByLabelText(messages.preview.acknowledge))
  await waitFor(() => expect(screen.getByLabelText(messages.schedules.consent)).toBeEnabled())
  expect(enable).toBeDisabled()
  fireEvent.click(screen.getByLabelText(messages.schedules.consent))
  expect(enable).toBeEnabled()
  fireEvent.click(enable)
  await screen.findByText(messages.schedules.approved)
  expect(screen.getByRole('button', { name: messages.preview.confirm })).toBeDisabled()
  const requests = fetchMock.mock.calls as unknown as [RequestInfo, RequestInit][]
  const sent = JSON.parse(String(requests.find(([url, init]) =>
    String(url).endsWith('/schedules') && init?.method === 'POST')?.[1].body))
  expect(sent.acknowledge_unattended).toBe(true)
  expect(sent.rule_id).toBe(7)
  expect(sent.preview.preview_token).toBe('token')
})

it('shows failures and execution history with job recovery links', async () => {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    if (String(input).endsWith('/runs')) return new Response(JSON.stringify([
      { id: 1, scheduled_for: '2026-09-30T07:00:00Z', status: 'partial', job_id: 12, error_code: null },
    ]))
    return new Response(JSON.stringify([{ id: 4, rule_id: 7, rule_revision: 2,
      enabled: false, status: 'needs_approval', notification: 'JOB_REQUIRES_ATTENTION',
      recurrence: { timezone: 'Europe/Paris', frequency: 'daily', hour: 9, minute: 0, weekday: 0 } }]))
  }))
  const cache = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={cache}><Schedules /></QueryClientProvider>)
  expect(await screen.findByRole('alert')).toHaveTextContent(messages.schedules.failure)
  expect(screen.getByRole('button', { name: messages.schedules.disable })).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: messages.schedules.history }))
  expect(await screen.findByRole('link', { name: messages.schedules.openJob })).toHaveAttribute('href', '/jobs/12')
})
