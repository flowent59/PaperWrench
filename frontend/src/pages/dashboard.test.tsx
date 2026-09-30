import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { DashboardSnapshot } from '@/api/types'

import { DashboardPage } from './dashboard'

const baseQuery = {
  filters: { root: { kind: 'group' as const, operator: 'and' as const, children: [
    { kind: 'condition' as const, field: { source: 'core' as const, name: 'added' },
      operator: 'greater_or_equal' as const, value: '2026-09-01' },
  ] } },
}

const snapshot: DashboardSnapshot = {
  range: '30d', start: '2026-09-01', end: '2026-10-01',
  generated_at: '2026-09-30T08:00:00Z', cache_ttl_seconds: 60,
  total_visible: 1234, documents_in_range: 12,
  trend: [{ start: '2026-09-01', end: '2026-09-02', count: 12, query: baseQuery }],
  breakdowns: [
    { dimension: 'correspondent', items: [{ id: 7, label: 'Hospital', count: 8, query: baseQuery }] },
    { dimension: 'document_type', items: [{ id: 3, label: 'Invoice', count: 10, query: baseQuery }] },
    { dimension: 'tags', items: [{ id: null, label: null, count: 2, query: baseQuery }] },
  ],
  custom_fields: [{ field_id: 11, label: 'Amount', data_type: 'integer', present: 7,
    missing: 5, present_query: baseQuery, missing_query: baseQuery }],
}

function response(body: unknown): Response {
  return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
}

function setup() {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => response({
    ...snapshot,
    range: String(input).includes('range=90d') ? '90d' : '30d',
  }))
  vi.stubGlobal('fetch', fetchMock)
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={queryClient}><MemoryRouter><DashboardPage /></MemoryRouter></QueryClientProvider>)
  return fetchMock
}

afterEach(() => vi.unstubAllGlobals())

describe('analytics dashboard', () => {
  it('renders localized numbers, breakdowns and exact Explorer links', async () => {
    setup()
    await screen.findByText('1,234')
    expect(screen.getByText('Hospital')).toBeInTheDocument()
    expect(screen.getByText('Amount')).toBeInTheDocument()
    const link = screen.getByRole('link', { name: /Hospital: 8/ })
    expect(link).toHaveAttribute('href', expect.stringContaining('/documents?quality_query='))
  })

  it('requests a new bounded snapshot when the range changes', async () => {
    const fetchMock = setup()
    await screen.findByText('1,234')
    fireEvent.click(screen.getByRole('button', { name: 'Last 90 days' }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('range=90d'), expect.anything(),
    ))
  })
})
