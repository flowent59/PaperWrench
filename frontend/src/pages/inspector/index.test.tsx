import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { DocumentDetail, EditRequest } from '@/api/types'
import { messages } from '@/i18n/messages'

import { InspectorPage } from './index'

const m = messages.inspector
function detail(overrides: Partial<DocumentDetail> = {}): DocumentDetail {
  return {
    id: 1,
    title: 'Relevé de vacations',
    correspondent: null,
    document_type: null,
    storage_path: null,
    tags: [],
    created: '2024-01-01',
    modified: null,
    added: null,
    archive_serial_number: 0,
    original_file_name: 'doc.pdf',
    owner: 1,
    user_can_change: true,
    revision: 'a'.repeat(64),
    catalog_revision: 'b'.repeat(64),
    definitions: [
      { id: 1, name: 'Période concernée', data_type: 'string', extra_data: {} },
      { id: 2, name: 'Montant', data_type: 'monetary', extra_data: {} },
      { id: 3, name: 'Validé', data_type: 'boolean', extra_data: {} },
      {
        id: 4,
        name: 'Option',
        data_type: 'select',
        extra_data: { select_options: [{ id: 'id-1', label: 'Choice' }] },
      },
    ],
    custom_fields: [
      {
        field_id: 1,
        kind: 'present',
        raw: 'Janvier',
        monetary: null,
        select_option_id: null,
        select_label: null,
      },
      {
        field_id: 2,
        kind: 'present',
        raw: 'EUR0.00',
        monetary: { currency: 'EUR', amount: '0.00' },
        select_option_id: null,
        select_label: null,
      },
      {
        field_id: 3,
        kind: 'present',
        raw: false,
        monetary: null,
        select_option_id: null,
        select_label: null,
      },
      {
        field_id: 4,
        kind: 'absent',
        raw: null,
        monetary: null,
        select_option_id: null,
        select_label: null,
      },
    ],
    editable_core_fields: [
      'title',
      'created',
      'archive_serial_number',
      'correspondent',
      'document_type',
      'storage_path',
      'tags',
    ],
    editable_custom_types: ['string', 'monetary', 'boolean', 'select'],
    ...overrides,
  }
}
function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}
function setup(
  doc = detail(),
  error?: { code: string; status: number },
  readError = false,
) {
  const requests: EditRequest[] = []
  const fetchMock = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const path = String(input)
    if (path.includes('/metadata/')) return json([])
    if (error && (readError || init?.method === 'PATCH'))
      return json(
        { error: { code: error.code, message: 'upstream', details: null } },
        error.status,
      )
    if (init?.method === 'PATCH') {
      const request = JSON.parse(String(init.body)) as EditRequest
      requests.push(request)
      const next = structuredClone(detail({ ...doc, revision: 'c'.repeat(64) }))
      if (typeof request.core.title === 'string') next.title = request.core.title.trim()
      for (const change of request.custom_changes) {
        const value = next.custom_fields.find((v) => v.field_id === change.field_id)!
        value.kind = change.kind
        value.raw = change.value ?? null
      }
      return json({
        before: doc,
        intended: request.core,
        document: next,
        external_atomicity: false,
        durable_history: false,
      })
    }
    return json(doc)
  })
  vi.stubGlobal('fetch', fetchMock)
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const invalidate = vi.spyOn(queryClient, 'invalidateQueries')
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/documents/1']}>
        <Routes>
          <Route path="/documents/:documentId" element={<InspectorPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return { requests, fetchMock, invalidate }
}
afterEach(() => vi.unstubAllGlobals())

describe('Inspector', () => {
  it('keeps an unset core reference null', async () => {
    const { requests } = setup()
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Edit Correspondent' }))
    await user.click(screen.getByRole('button', { name: m.save }))
    await screen.findByRole('status')
    expect(requests[0]?.core).toEqual({ correspondent: null })
  })

  it('requires a fresh successful read before another edit, even for a no-op', async () => {
    const { requests } = setup()
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Edit Title' }))
    await user.click(screen.getByRole('button', { name: m.save }))
    await screen.findByRole('status')
    expect(screen.queryByRole('button', { name: 'Edit Title' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: m.reload }))
    await user.click(await screen.findByRole('button', { name: 'Edit Title' }))
    expect(screen.getByRole('textbox', { name: 'Title' })).toBeInTheDocument()
    expect(requests).toHaveLength(1)
  })
  it('shows metadata and cancels without a write', async () => {
    const { requests } = setup()
    const user = userEvent.setup()
    expect(await screen.findByRole('heading', { name: 'Relevé de vacations' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: m.back })).toHaveAttribute(
      'href',
      '/documents',
    )
    expect(screen.getByText('EUR0.00')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Edit Title' }))
    await user.type(screen.getByRole('textbox', { name: 'Title' }), ' discarded')
    await user.click(screen.getByRole('button', { name: m.cancel }))
    expect(requests).toHaveLength(0)
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
  })

  it('shows actual normalization and invalidates grid/detail/count caches', async () => {
    const { requests, invalidate } = setup()
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Edit Title' }))
    await user.clear(screen.getByRole('textbox', { name: 'Title' }))
    await user.type(screen.getByRole('textbox', { name: 'Title' }), '  New title  ')
    await user.click(screen.getByRole('button', { name: m.save }))
    const receipt = await screen.findByRole('status')
    expect(within(receipt).getByText(m.stored)).toBeInTheDocument()
    expect(requests).toHaveLength(1)
    expect(requests[0]?.core.title).toBe('  New title  ')
    expect(receipt.textContent).toContain('New title')
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['documents'] })
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['filters', 'count'] })
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: ['document', 1],
      refetchType: 'none',
    })
  })

  it.each([
    ['Montant', 'EUR12345678901234567890.12', 'present', 'EUR12345678901234567890.12'],
    ['Validé', 'false', 'present', false],
    ['Option', 'id-1', 'present', 'id-1'],
    ['Période concernée', '', 'present', ''],
    ['Période concernée', '', 'null', undefined],
    ['Période concernée', '', 'absent', undefined],
  ])('uses typed controls for %s (%s, %s)', async (name, input, kind, expected) => {
    const { requests } = setup()
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: `Edit ${name}` }))
    await user.selectOptions(
      screen.getByRole('combobox', { name: m.state }),
      String(kind),
    )
    if (kind === 'present') {
      const control = screen.getByLabelText(String(name))
      if (name === 'Option' || name === 'Validé')
        await user.selectOptions(control, String(input))
      else {
        fireEvent.change(control, { target: { value: input } })
      }
    }
    expect(screen.getByRole('button', { name: m.save })).toBeDisabled()
    await user.click(screen.getByRole('checkbox', { name: m.acknowledge }))
    await user.click(screen.getByRole('button', { name: m.save }))
    await screen.findByRole('status')
    expect(requests[0]?.custom_changes[0]?.kind).toBe(kind)
    expect(requests[0]?.custom_changes[0]?.value).toBe(expected)
    expect(requests[0]?.acknowledge_external_race).toBe(true)
  })

  it.each([false, null])(
    'does not offer editing when permission is %s',
    async (permission) => {
      setup(detail({ user_can_change: permission }))
      await screen.findByText(m.readOnly)
      expect(screen.queryByRole('button', { name: /^Edit / })).not.toBeInTheDocument()
    },
  )

  it.each([
    ['CONFLICT', 409, m.conflict],
    ['PAPERLESS_UNAUTHORIZED', 502, m.unauthorized],
    ['PAPERLESS_FORBIDDEN', 502, m.forbidden],
    ['NOT_FOUND', 404, m.notFound],
    ['PAPERLESS_UNREACHABLE', 502, m.uncertain],
  ])('keeps the draft and blocks resubmission on %s', async (code, status, message) => {
    const { fetchMock } = setup(detail(), { code: String(code), status: Number(status) })
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Edit Title' }))
    await user.type(screen.getByRole('textbox', { name: 'Title' }), ' draft')
    await user.click(screen.getByRole('button', { name: m.save }))
    await screen.findByText(String(message))
    expect(screen.getByRole('textbox', { name: 'Title' })).toHaveValue(
      'Relevé de vacations draft',
    )
    expect(screen.getByRole('button', { name: m.save })).toBeDisabled()
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.filter(([, init]) => init?.method === 'PATCH'),
      ).toHaveLength(1),
    )
  })

  it.each([
    ['PAPERLESS_UNAUTHORIZED', 502, m.unauthorized],
    ['PAPERLESS_FORBIDDEN', 502, m.forbidden],
    ['NOT_FOUND', 404, m.notFound],
  ])('renders read failure %s', async (code, status, message) => {
    setup(detail(), { code: String(code), status: Number(status) }, true)
    expect(await screen.findByRole('alert')).toHaveTextContent(String(message))
  })
})
