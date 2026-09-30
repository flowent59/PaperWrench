import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import type { Transformation } from '@/api/types'
import { messages } from '@/i18n/messages'

import { SavedRules } from './saved-rules'

afterEach(() => vi.unstubAllGlobals())

it('saves a filtered rule and requests its own mandatory preview', async () => {
  const spec: Transformation = {
    targets: { source: 'dataset', query: { search: { mode: 'title', text: 'invoice' } } },
    operations: [{ operation: 'set', field: { source: 'core', name: 'title' }, value: 'New' }],
  }
  const saved = { id: 7, name: 'Rename invoices', description: null, revision: 1,
    created_at: '2026-09-30T12:00:00Z', updated_at: '2026-09-30T12:00:00Z',
    definition: { target: { kind: 'filter', query: spec.targets.source === 'dataset'
      ? spec.targets.query : {} }, operations: spec.operations } }
  let rules: typeof saved[] = []
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input)
    if (path.endsWith('/rules') && init?.method === 'POST') {
      rules = [saved]
      return new Response(JSON.stringify(saved), { status: 201 })
    }
    if (path.endsWith('/rules')) return new Response(JSON.stringify(rules))
    if (path.endsWith('/collections')) return new Response(JSON.stringify([]))
    if (path.endsWith('/rules/7/preview')) return new Response(JSON.stringify({
      id: 'a'.repeat(32), preview_token: 'token', transformation: spec,
      version: 1, created_at: saved.created_at,
      expires_at: new Date(Date.now() + 600_000).toISOString(),
      matched: 1, evaluated: 1, changed: 1, unchanged: 0, errors: 0,
      selection_fingerprint: 's', spec_fingerprint: 'p', target_fingerprint: 't',
      result_fingerprint: 'r', confirmed: false,
    }), { status: 201 })
    if (path.includes('/previews/')) return new Response(JSON.stringify({
      items: [], page: 1, page_size: 25, total: 0, page_count: 0,
    }))
    return new Response(JSON.stringify([]))
  })
  vi.stubGlobal('fetch', fetchMock)
  const cache = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={cache}><SavedRules build={() => spec} /></QueryClientProvider>)
  fireEvent.change(screen.getByLabelText(messages.rules.name), {
    target: { value: 'Rename invoices' },
  })
  fireEvent.click(screen.getByRole('button', { name: messages.rules.save }))
  await screen.findByText('Rename invoices', { selector: 'strong' })
  expect(JSON.parse(String(fetchMock.mock.calls.find(([url, options]) =>
    String(url).endsWith('/rules') && options?.method === 'POST')?.[1]?.body)).definition)
    .toEqual(saved.definition)
  fireEvent.click(screen.getByRole('button', { name: messages.preview.create }))
  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) =>
    String(url).endsWith('/rules/7/preview'))).toBe(true))
  expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith('/previews'))).toBe(false)
})
