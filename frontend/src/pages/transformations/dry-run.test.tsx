import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { CreatedPreview, PreviewPage, Transformation } from '@/api/types'
import { messages } from '@/i18n/messages'
import { formatNumber } from '@/i18n/format'

import { DryRun } from './dry-run'
import { valueText } from './value'

const m = messages.preview
const spec: Transformation = { targets: { source: 'ids', document_ids: [1, 2] },
  operations: [{ operation: 'set', field: { source: 'core', name: 'title' }, value: 'Été' }] }

function preview(overrides: Partial<CreatedPreview> = {}): CreatedPreview {
  return { id: 'preview-1', version: 1, created_at: new Date().toISOString(),
    expires_at: new Date(Date.now() + 60000).toISOString(), matched: 10000, evaluated: 10000,
    changed: 9000, unchanged: 1000, errors: 0, confirmed: false, selection_fingerprint: 'selection',
    spec_fingerprint: 'spec', target_fingerprint: 'targets', result_fingerprint: 'results',
    preview_token: 'opaque-token', ...overrides }
}

function page(number = 1): PreviewPage {
  return { items: [{ document_id: number, title: `Document ${number}`, status: 'change', issue: null,
    changes: [{ field: { source: 'core', name: 'title' }, operation: 'set', status: 'change',
      before: { kind: 'present', raw: 'Ancien' }, intended: { kind: 'present', raw: 'Été' }, issue: null }] }],
  page: number, page_size: 25, total: 10000, page_count: 400 }
}

function setup(value = preview(), confirmationStatus = 200, transformation = spec) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input)
    if (init?.method === 'DELETE') return new Response(null, { status: 204 })
    if (path.endsWith('/jobs')) return new Response(JSON.stringify(confirmationStatus === 200
      ? { id: 42 } : { error: { code: 'PREVIEW_STALE', message: 'Expired on server' } }),
    { status: confirmationStatus })
    if (path.includes('/documents?')) return new Response(JSON.stringify(page(path.includes('page=2&') ? 2 : 1)))
    return new Response(JSON.stringify(value), { status: 201 })
  })
  vi.stubGlobal('fetch', fetchMock)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const view = render(<QueryClientProvider client={client}><DryRun build={() => transformation} /></QueryClientProvider>)
  return { fetchMock, view, client }
}

afterEach(() => vi.unstubAllGlobals())

describe('M7 Dry Run', () => {
  it('requires an additional acknowledgement when values are removed', async () => {
    setup(preview(), 200, { ...spec, operations: [{ operation: 'clear',
      field: { source: 'core', name: 'title' } }] })
    fireEvent.click(screen.getByRole('button', { name: m.create }))
    await screen.findByText('Ancien')
    fireEvent.click(screen.getByRole('checkbox', { name: m.acknowledge }))
    expect(screen.getByRole('button', { name: m.confirm })).toBeDisabled()
    fireEvent.click(screen.getByRole('checkbox', { name: m.acknowledgeClear }))
    expect(screen.getByRole('button', { name: m.confirm })).toBeEnabled()
  })

  it('requires a separate acknowledgement before a custom-field Apply', async () => {
    const { fetchMock } = setup(preview(), 200, { ...spec, operations: [{ operation: 'set',
      field: { source: 'custom_field', field_id: 1 }, value: 'Août' }] })
    fireEvent.click(screen.getByRole('button', { name: m.create }))
    await screen.findByText('Ancien')
    fireEvent.click(screen.getByRole('checkbox', { name: m.acknowledge }))
    expect(screen.getByRole('button', { name: m.confirm })).toBeDisabled()
    fireEvent.click(screen.getByRole('checkbox', { name: messages.jobs.acknowledgeRace }))
    fireEvent.click(screen.getByRole('button', { name: m.confirm }))
    await screen.findByText(m.confirmed)
    const call = fetchMock.mock.calls.find(([url]) => String(url).endsWith('/jobs'))
    expect(JSON.parse(String(call?.[1]?.body)).acknowledge_external_race).toBe(true)
  })

  it('directs a lost Apply response to History without automatically resubmitting', async () => {
    const { fetchMock } = setup()
    fireEvent.click(screen.getByRole('button', { name: m.create }))
    await screen.findByText('Ancien')
    fetchMock.mockRejectedValueOnce(new TypeError('offline'))
    fireEvent.click(screen.getByRole('checkbox', { name: m.acknowledge }))
    fireEvent.click(screen.getByRole('button', { name: m.confirm }))
    await screen.findByText(messages.jobs.uncertainApply)
    expect(screen.getByRole('button', { name: m.confirm })).toBeDisabled()
    expect(screen.getByRole('link', { name: messages.jobs.open })).toHaveAttribute('href', '/history')
    expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith('/jobs'))).toHaveLength(1)
  })

  it('shows counts and pages, then explicitly creates one Job with the reviewed token', async () => {
    const { fetchMock } = setup()
    expect(screen.queryByRole('button', { name: m.confirm })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: m.create }))
    await screen.findByText('Ancien')
    expect(screen.getByText(m.changed, { selector: 'dt' }).parentElement).toHaveTextContent(formatNumber(9000))
    expect(screen.getByText(m.unchanged, { selector: 'dt' }).parentElement).toHaveTextContent(formatNumber(1000))
    expect(screen.getByRole('button', { name: m.confirm })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: m.next }))
    await screen.findByText('Document 2 (#2)')
    expect(screen.queryByText('Document 1 (#1)')).not.toBeInTheDocument()
    expect(screen.getByText('Page 2 / 400')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('checkbox', { name: m.acknowledge }))
    fireEvent.click(screen.getByRole('button', { name: m.confirm }))
    await screen.findByText(m.confirmed)
    expect(screen.getByRole('button', { name: m.confirm })).toBeDisabled()
    expect(screen.getByRole('link', { name: messages.jobs.open })).toHaveAttribute('href', '/jobs/42')
    const call = fetchMock.mock.calls.find(([url]) => String(url).endsWith('/jobs'))
    expect(JSON.parse(String(call?.[1]?.body))).toMatchObject({ transformation: spec,
      preview_token: 'opaque-token', target_fingerprint: 'targets', result_fingerprint: 'results',
      version: 1, acknowledge: true })
    expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith('/jobs'))).toHaveLength(1)
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith('/confirm'))).toBe(false)
  })

  it('shows exact unresolved rows and prevents confirmation when any document has errors', async () => {
    setup(preview({ errors: 1 }))
    const fetchMock = vi.mocked(fetch)
    const errorPage = page()
    errorPage.items[0]!.status = 'error'
    errorPage.items[0]!.changes[0]!.intended = null
    errorPage.items[0]!.changes[0]!.issue = { code: 'TEMPLATE_UNRESOLVED',
      message: 'Template source is absent, null or empty', field_key: 'custom_field:1' }
    fetchMock.mockImplementation(async (url) => new Response(JSON.stringify(String(url).includes('/documents?')
      ? errorPage : preview({ errors: 1 }))))
    fireEvent.click(screen.getByRole('button', { name: m.create }))
    await screen.findByText(/A template value could not be resolved\./u)
    expect(screen.getByText(m.fixErrors)).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: m.acknowledge })).toBeDisabled()
    expect(screen.getByRole('button', { name: m.confirm })).toBeDisabled()
  })

  it('refuses expired results locally and a stale confirmation rejected by the server', async () => {
    const first = setup(preview({ expires_at: new Date(Date.now() - 1).toISOString() }))
    fireEvent.click(screen.getByRole('button', { name: m.create }))
    await screen.findByText(m.expired)
    expect(screen.queryByRole('button', { name: m.confirm })).not.toBeInTheDocument()
    first.view.unmount()
    setup(preview(), 409)
    fireEvent.click(screen.getByRole('button', { name: m.create }))
    await screen.findByText('Ancien')
    fireEvent.click(screen.getByRole('checkbox', { name: m.acknowledge }))
    fireEvent.click(screen.getByRole('button', { name: m.confirm }))
    await screen.findByText(m.expired)
    expect(screen.queryByRole('button', { name: m.confirm })).not.toBeInTheDocument()
  })

  it('filters on the server and resets page navigation', async () => {
    const { fetchMock } = setup()
    fireEvent.click(screen.getByRole('button', { name: m.create }))
    await screen.findByText('Ancien')
    fireEvent.click(screen.getByRole('button', { name: m.next }))
    await screen.findByText('Document 2 (#2)')
    fireEvent.change(screen.getByLabelText(m.show), { target: { value: 'error' } })
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) =>
      String(url).endsWith('page=1&page_size=25&status=error'))).toBe(true))
  })

  it('drops confirmation when editing the specification remounts the panel', async () => {
    const { view, client, fetchMock } = setup()
    fireEvent.click(screen.getByRole('button', { name: m.create }))
    await screen.findByText('Ancien')
    fireEvent.click(screen.getByRole('checkbox', { name: m.acknowledge }))
    view.rerender(<QueryClientProvider client={client}><DryRun key="edited-spec" build={() => spec} /></QueryClientProvider>)
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
    expect(within(screen.getByRole('region', { name: m.title })).queryByText('Ancien')).not.toBeInTheDocument()
    await waitFor(() => expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'DELETE')).toBe(true))
  })

  it('renders ABSENT, NULL, empty, zero, false, money, dates and Select labels distinctly', () => {
    expect(valueText({ kind: 'absent', raw: null })).toBe(messages.transformations.absentValue)
    expect(valueText({ kind: 'null', raw: null })).toBe(messages.transformations.nullValue)
    expect(valueText({ kind: 'present', raw: '' })).toBe(messages.transformations.emptyString)
    expect(valueText({ kind: 'present', raw: 0 })).toBe('0')
    expect(valueText({ kind: 'present', raw: false })).toBe('false')
    expect(valueText({ kind: 'present', raw: 'EUR0.00', monetary: { currency: 'EUR', amount: '0.00' } })).toBe('EUR0.00')
    expect(valueText({ kind: 'present', raw: '2026-09-24' })).toBe('2026-09-24')
    expect(valueText({ kind: 'present', raw: 'opaque', select_option_id: 'opaque', select_label: 'Été' })).toBe('Été')
    expect(valueText({ kind: 'present', raw: 'opaque', select_option_id: 'opaque', select_label: null })).toBe('Unknown option (#opaque)')
  })
})
