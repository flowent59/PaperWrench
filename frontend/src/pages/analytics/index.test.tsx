import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { CustomFieldReport, FilterCapabilities } from '@/api/types'

import { AnalyticsPage } from './index'

const capabilities: FilterCapabilities = {
  fields: [],
  grouping: { and_supported: true, or_custom_fields_supported: true,
    or_core_fields_supported: false, or_mixed_supported: false, not_supported: false,
    max_conditions: 50, max_depth: 10, custom_field_max_depth: 10,
    custom_field_max_conditions: 20 },
  search_modes: [], operator_semantics: {},
}

const query = { filters: { root: { kind: 'group' as const, operator: 'and' as const,
  children: [{ kind: 'condition' as const, field: { source: 'custom_field' as const,
    field_id: 11 }, operator: 'has_value' as const }] } } }

const report: CustomFieldReport = {
  field_id: 11, field_name: 'Balance', data_type: 'monetary', range: '365d',
  start: '2025-10-01', end: '2026-10-01', group_by: 'month',
  aggregation: 'latest_snapshot', additive: false, matched_documents: 5,
  valued_documents: 3,
  groups: [{ key: '2026-09', label: '2026-09', document_count: 3,
    values: [{ value: '1200.50', currency: 'EUR' }], query }],
  missing: [
    { kind: 'absent', count: 1, query },
    { kind: 'null', count: 1, query },
    { kind: 'invalid', count: 0, query: null },
  ],
}

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), { status: 200,
    headers: { 'Content-Type': 'application/json' } })
}

function setup() {
  const calls: string[] = []
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input); calls.push(path)
    if (path.endsWith('/metadata/custom-fields')) return json([
      { id: 11, name: 'Balance', data_type: 'monetary', extra_data: {} },
      { id: 12, name: 'Comment', data_type: 'string', extra_data: {} },
    ])
    if (path.endsWith('/filters/capabilities')) return json(capabilities)
    if (path.includes('/metadata/')) return json([])
    if (path.endsWith('/analytics/reports')) return json(report)
    if (path.endsWith('/analytics/reports/export')) return new Response('field,group\nBalance,2026-09',
      { status: 200, headers: { 'Content-Type': 'text/csv' } })
    throw new Error(`Unexpected request: ${path}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={queryClient}><MemoryRouter><AnalyticsPage /></MemoryRouter></QueryClientProvider>)
  return { calls, fetchMock }
}

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

describe('custom-field analytics', () => {
  it('offers reportable fields and explains non-additive snapshots', async () => {
    setup()
    const user = userEvent.setup()
    const field = await screen.findByLabelText('Custom field')
    await screen.findByRole('option', { name: 'Balance' })
    expect(screen.queryByRole('option', { name: 'Comment' })).not.toBeInTheDocument()
    await user.selectOptions(field, '11')
    await user.selectOptions(screen.getByLabelText('Numeric meaning'), 'snapshot')
    const run = screen.getByRole('button', { name: 'Run report' })
    await waitFor(() => expect(run).toBeEnabled())
    fireEvent.click(run)
    await screen.findByText('Snapshot values are non-additive. Do not total the groups.')
    expect(screen.getByText('€1,200.50')).toBeInTheDocument()
    expect(screen.getByText('Field absent')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /2026/ })).toHaveAttribute(
      'href', expect.stringContaining('/documents?quality_query='),
    )
  })

  it('exports the currently configured authorized report as CSV', async () => {
    const user = userEvent.setup()
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
    const createObjectURL = vi.fn(() => 'blob:report')
    const revokeObjectURL = vi.fn()
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: createObjectURL })
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: revokeObjectURL })
    const { calls } = setup()
    const field = await screen.findByLabelText('Custom field')
    await screen.findByRole('option', { name: 'Balance' })
    await user.selectOptions(field, '11')
    const run = screen.getByRole('button', { name: 'Run report' })
    await waitFor(() => expect(run).toBeEnabled())
    fireEvent.click(run)
    await screen.findByRole('link', { name: /2026/ })
    fireEvent.click(screen.getByRole('button', { name: 'Export CSV' }))
    await waitFor(() => expect(calls.some((path) => path.endsWith('/analytics/reports/export'))).toBe(true))
    expect(createObjectURL).toHaveBeenCalled()
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:report')
  })
})
