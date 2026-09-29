import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { messages } from '@/i18n/messages'
import type { TransformationTarget } from '@/api/types'

import { TransformationsPage } from './index'

const m = messages.transformations
const fields = [{ id: 7, name: 'Période concernée', data_type: 'string', extra_data: {} }]

function renderPage(initialTargets?: TransformationTarget) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={queryClient}><TransformationsPage initialTargets={initialTargets} /></QueryClientProvider>)
}

afterEach(() => vi.unstubAllGlobals())

describe('M6 authoring page', () => {
  it('preserves the dataset identity passed from Explorer, including ordering', async () => {
    const targets: TransformationTarget = { source: 'dataset', query: {
      search: { mode: 'content', text: 'été' }, ordering: '-created',
      filters: { root: { kind: 'group', operator: 'and', children: [] } },
    } }
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      void init
      if (String(input).endsWith('/previews')) return new Response(JSON.stringify({
        error: { code: 'VALIDATION_ERROR', message: 'Captured selection' },
      }), { status: 422 })
      return new Response(JSON.stringify(String(input).endsWith('/metadata/custom-fields') ? fields : []))
    })
    vi.stubGlobal('fetch', fetchMock)
    renderPage(targets)
    await screen.findAllByRole('option', { name: 'Période concernée' })
    expect(screen.getByLabelText(messages.preview.ordering)).toHaveValue('-created')
    fireEvent.change(screen.getByLabelText('{Période concernée}'), { target: { value: 'custom_field:7' } })
    fireEvent.click(screen.getByRole('button', { name: messages.preview.create }))
    await screen.findByText('The submitted data is invalid.')
    const call = fetchMock.mock.calls.find(([url]) => String(url).endsWith('/previews'))
    expect(JSON.parse(String(call?.[1]?.body)).targets).toEqual(targets)
  })

  it('prefills explicit Explorer IDs without fetching the entire dataset', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify([])))
    vi.stubGlobal('fetch', fetchMock)
    renderPage({ source: 'ids', document_ids: [7, 900] })
    expect(screen.getByLabelText(m.ids)).toHaveValue('7, 900')
    expect(screen.getByLabelText(m.explicitIds)).toBeChecked()
    expect(fetchMock.mock.calls.length).toBeGreaterThan(0)
  })

  it('serializes stable template binding and explicit target IDs', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input)
      if (path.endsWith('/metadata/custom-fields')) return new Response(JSON.stringify(fields), { status: 200 })
      if (path.endsWith('/transformations/validate')) return new Response(JSON.stringify({
        valid: true, issues: [],
      }), { status: 200 })
      if (path.includes('/transformations/documents/')) return new Response(JSON.stringify({
        document_id: 100,
        changes: [{ field: { source: 'core', name: 'title' }, operation: 'template',
          status: 'change', before: { kind: 'present', raw: 'Old' },
          intended: { kind: 'present', raw: 'Relevé de vacations – Juillet' }, issue: null }],
      }), { status: 200 })
      void init
      return new Response(JSON.stringify([]), { status: 200 })
    })
    vi.stubGlobal('fetch', fetchMock)
    renderPage()
    await screen.findAllByRole('option', { name: 'Période concernée' })
    fireEvent.change(screen.getByLabelText(m.ids), { target: { value: '100' } })
    fireEvent.change(screen.getByLabelText('{Période concernée}'), {
      target: { value: 'custom_field:7' },
    })
    fireEvent.click(screen.getByRole('button', { name: m.evaluate }))
    await screen.findByText(/Relevé de vacations – Juillet/u)
    const call = fetchMock.mock.calls.find(([input]) => String(input).includes('/transformations/documents/'))
    expect(call).toBeDefined()
    const body = JSON.parse(String(call?.[1]?.body))
    expect(body.targets).toEqual({ source: 'ids', document_ids: [100] })
    expect(body.operations[0].bindings['Période concernée'].field_id).toBe(7)
    expect(call?.[1]?.method).toBe('POST')
  })

  it('switches target source and reports missing template bindings', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) =>
      new Response(JSON.stringify(String(input).endsWith('/metadata/custom-fields') ? fields : []),
        { status: 200 })))
    renderPage()
    fireEvent.click(screen.getByLabelText(m.dataset))
    expect(screen.getByText(m.search)).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText(m.documentId), { target: { value: '100' } })
    fireEvent.click(screen.getByRole('button', { name: m.evaluate }))
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Choose a field'))
  })

  it('shows server validation errors before requesting a document', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input)
      if (path.endsWith('/metadata/custom-fields')) return new Response(JSON.stringify(fields))
      if (path.endsWith('/transformations/validate')) return new Response(JSON.stringify({
        valid: false, issues: [{ code: 'UNKNOWN_FIELD', message: 'Field was deleted',
          field_key: 'custom_field:7' }],
      }))
      return new Response(JSON.stringify([]))
    })
    vi.stubGlobal('fetch', fetchMock)
    renderPage()
    await screen.findAllByRole('option', { name: 'Période concernée' })
    fireEvent.change(screen.getByLabelText(m.ids), { target: { value: '100' } })
    fireEvent.change(screen.getByLabelText('{Période concernée}'), {
      target: { value: 'custom_field:7' },
    })
    fireEvent.click(screen.getByRole('button', { name: m.evaluate }))
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(
      'This field is no longer available.',
    ))
    expect(fetchMock.mock.calls.some(([input]) => String(input).includes('/documents/'))).toBe(false)
  })
})
