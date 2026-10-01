import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, it, vi } from 'vitest'

import { messages } from '@/i18n/messages'

import { RollbackReview } from './rollback'

const m = messages.rollback

function setup({ changed = 1, custom = false, failure = 0 } = {}) {
  const mock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url.includes('/rollback-candidates?')) {
      const page = new URL(url, 'http://localhost').searchParams.get('page')
      const items = page === '2' ? [
        { document_id: 3, title: 'Third', status: 'restored' },
        { document_id: 4, title: 'Fourth', status: 'available' },
      ] : [
        { document_id: 1, title: 'Vacation', status: 'available' },
        { document_id: 2, title: 'External', status: 'available' },
      ]
      return new Response(JSON.stringify({ items, page_count: 2, total: 4 }))
    }
    if (url.endsWith('/rollback-preview')) return new Response(JSON.stringify({
      id: 'preview', preview_token: 'opaque', version: 1, changed, unchanged: 1, errors: 1,
      expires_at: new Date(Date.now() + 60000).toISOString(), confirmed: false,
      target_fingerprint: 'targets', result_fingerprint: 'results', requires_external_race_ack: custom,
    }))
    if (url.includes('/documents?')) return new Response(JSON.stringify({ items: [{
      document_id: 1, title: 'Vacation', status: 'change', issue: null,
      changes: [{ field: { source: 'core', name: 'title' }, before: { kind: 'present', raw: 'New' },
        intended: { kind: 'present', raw: 'Before' } }], excluded_operations: { 'custom:3': 'NO_PROVEN_WRITE' },
    }, { document_id: 2, title: 'External', status: 'error', changes: [],
      issue: { code: 'ROLLBACK_CONFLICT', message: 'No field of this document will be restored.' },
      excluded_operations: { 'core:title': 'MANUAL_REVIEW' } }], page_count: 2 }))
    if (url.endsWith('/rollback') && init?.method === 'POST') {
      if (failure) return new Response(JSON.stringify({ error: { code: failure === 409 ? 'PREVIEW_STALE' : 'INTERNAL_ERROR', message: 'Rejected' } }), { status: failure })
      return new Response(JSON.stringify({ id: 2 }))
    }
    return new Response(null, { status: 204 })
  })
  vi.stubGlobal('fetch', mock)
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <MemoryRouter><RollbackReview jobId={1} /></MemoryRouter>
  </QueryClientProvider>)
  return mock
}
afterEach(() => vi.unstubAllGlobals())

it('previews field values, exclusions and conflicts before explicit confirmation; follows linked Job', async () => {
  const mock = setup({ custom: true })
  expect(mock).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: m.preview }))
  await screen.findByText(/New → Before/)
  expect(screen.getByText(/The document changed after the original write/)).toBeInTheDocument()
  expect(screen.getByText(/Review this document manually/)).toBeInTheDocument()
  const confirm = screen.getByRole('button', { name: m.confirm })
  expect(confirm).toBeDisabled()
  fireEvent.click(screen.getByLabelText(m.acknowledge))
  expect(confirm).toBeDisabled()
  fireEvent.click(screen.getByLabelText(messages.jobs.acknowledgeRace))
  fireEvent.click(confirm)
  expect(await screen.findByRole('link', { name: 'Rollback Job #2' })).toHaveAttribute('href', '/jobs/2')
  expect(confirm).toBeDisabled()
  const request = mock.mock.calls.find(([url]) => String(url).endsWith('/rollback'))?.[1]
  expect(JSON.parse(String(request?.body))).toEqual({ preview_id: 'preview', preview_token: 'opaque',
    target_fingerprint: 'targets', result_fingerprint: 'results', version: 1, acknowledge: true,
    acknowledge_external_race: true })
})

it('pages results on the server and refuses a preview without candidates', async () => {
  const mock = setup({ changed: 0 })
  fireEvent.click(screen.getByRole('button', { name: m.preview }))
  await screen.findByText(m.none)
  await waitFor(() => expect(screen.getByRole('button', { name: messages.preview.next })).toBeEnabled())
  fireEvent.click(screen.getByRole('button', { name: messages.preview.next }))
  await waitFor(() => expect(mock.mock.calls.some(([url]) => String(url).includes('page=2&page_size=25'))).toBe(true))
  fireEvent.click(screen.getByLabelText(m.acknowledge))
  expect(screen.getByRole('button', { name: m.confirm })).toBeDisabled()
})

it.each([409, 500])('does not replay stale or uncertain confirmation (%i)', async failure => {
  const mock = setup({ failure })
  fireEvent.click(screen.getByRole('button', { name: m.preview }))
  await screen.findByText(/New → Before/)
  fireEvent.click(screen.getByLabelText(m.acknowledge))
  fireEvent.click(screen.getByRole('button', { name: m.confirm }))
  await waitFor(() => expect(screen.getByRole('button', { name: m.confirm })).toBeDisabled())
  await screen.findByText(failure === 409 ? messages.preview.expired : m.uncertain, { exact: false })
  expect(mock.mock.calls.filter(([url]) => String(url).endsWith('/rollback'))).toHaveLength(1)
})

it('keeps selected documents across pages and sends only that subset to preview', async () => {
  const mock = setup()
  fireEvent.click(screen.getByLabelText(m.selected))
  expect(screen.getByRole('button', { name: m.preview })).toBeDisabled()
  fireEvent.click(await screen.findByLabelText(/Vacation \(#1\)/))
  fireEvent.click(screen.getByRole('button', { name: messages.preview.next }))
  fireEvent.click(await screen.findByLabelText(/Fourth \(#4\)/))
  expect(screen.getByText(m.selectedCount.replace('{count}', '2'))).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: m.preview }))
  await screen.findByText(/New → Before/)
  const sent = mock.mock.calls.find(([url, init]) =>
    String(url).endsWith('/rollback-preview') && init?.method === 'POST')?.[1]
  expect(JSON.parse(String(sent?.body))).toEqual({ document_ids: [1, 4] })
  expect(screen.getByText(m.before)).toBeInTheDocument()
  expect(screen.getByText(m.current)).toBeInTheDocument()
  expect(screen.getByText(m.restored)).toBeInTheDocument()
  fireEvent.click(screen.getByLabelText(/Fourth \(#4\)/))
  expect(screen.queryByText(m.before)).not.toBeInTheDocument()
})
