import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { DocumentPage } from '@/api/types'

import { ExplorerPage } from './index'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function makePage(overrides: Partial<DocumentPage> = {}): DocumentPage {
  return {
    items: [
      {
        id: 1,
        title: 'Invoice #1',
        correspondent: { id: 10, name: 'Acme Corp' },
        document_type: { id: 20, name: 'Invoice' },
        tags: [{ id: 30, name: 'Important' }],
        created: '2024-01-15T10:00:00Z',
        modified: '2024-01-16T10:00:00Z',
        added: '2024-01-15T10:00:00Z',
        archive_serial_number: 123,
        custom_fields: [],
        user_can_change: true,
      },
      {
        id: 2,
        title: 'Invoice #2',
        correspondent: null,
        document_type: null,
        tags: [],
        created: '2024-02-01T10:00:00Z',
        modified: null,
        added: null,
        archive_serial_number: null,
        custom_fields: [],
        user_can_change: false,
      },
    ],
    page: 1,
    page_size: 100,
    total: 2,
    page_count: 1,
    ...overrides,
  }
}

function renderWithProviders(children: React.ReactNode) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>,
  )
}

/** Routes a mocked fetch by URL path, so each test only wires what it needs. */
function mockFetchRouter(
  handlers: Record<string, () => Response> = {},
): ReturnType<typeof vi.fn> {
  const defaultHandlers: Record<string, () => Response> = {
    '/api/v1/metadata/tags': () => jsonResponse([]),
    '/api/v1/metadata/correspondents': () => jsonResponse([]),
    '/api/v1/metadata/document-types': () => jsonResponse([]),
    '/api/v1/metadata/custom-fields': () => jsonResponse([]),
    '/api/v1/documents': () => jsonResponse(makePage()),
  }
  const merged = { ...defaultHandlers, ...handlers }

  const spy = vi.fn((input: RequestInfo | URL) => {
    const url = typeof input === 'string' ? input : input.toString()
    const path = url.split('?')[0] ?? url
    const handler = merged[path]
    if (handler === undefined) {
      throw new Error(`Unhandled fetch in test: ${url}`)
    }
    return Promise.resolve(handler())
  })
  vi.stubGlobal('fetch', spy)
  return spy
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('ExplorerPage', () => {
  it('renders the loading state before data arrives', () => {
    mockFetchRouter({
      '/api/v1/documents': () => jsonResponse(makePage()),
    })
    renderWithProviders(<ExplorerPage />)

    expect(screen.getByText('Loading documents...')).toBeInTheDocument()
  })

  it('renders document rows once loaded', async () => {
    mockFetchRouter()
    renderWithProviders(<ExplorerPage />)

    expect(await screen.findByText('Invoice #1')).toBeInTheDocument()
    expect(screen.getByText('Invoice #2')).toBeInTheDocument()
    expect(screen.getByText('Acme Corp')).toBeInTheDocument()
    expect(screen.getByText('Important')).toBeInTheDocument()
  })

  it('renders the empty state when no documents match', async () => {
    mockFetchRouter({
      '/api/v1/documents': () =>
        jsonResponse(makePage({ items: [], total: 0, page_count: 0 })),
    })
    renderWithProviders(<ExplorerPage />)

    expect(await screen.findByText('No documents')).toBeInTheDocument()
  })

  it('renders the error state and allows retrying', async () => {
    let calls = 0
    mockFetchRouter({
      '/api/v1/documents': () => {
        calls += 1
        return calls === 1
          ? jsonResponse({ error: { code: 'INTERNAL_ERROR', message: 'boom' } }, 500)
          : jsonResponse(makePage())
      },
    })
    renderWithProviders(<ExplorerPage />)

    expect(await screen.findByText('Could not load documents')).toBeInTheDocument()

    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Retry' }))

    expect(await screen.findByText('Invoice #1')).toBeInTheDocument()
  })

  it('renders an unresolved correspondent reference as "Unknown (#id)"', async () => {
    mockFetchRouter({
      '/api/v1/documents': () =>
        jsonResponse(
          makePage({
            items: [
              {
                id: 5,
                title: 'Orphaned document',
                correspondent: { id: 999, name: null },
                document_type: null,
                tags: [],
                created: null,
                modified: null,
                added: null,
                archive_serial_number: null,
                custom_fields: [],
                user_can_change: null,
              },
            ],
            total: 1,
            page_count: 1,
          }),
        ),
    })
    renderWithProviders(<ExplorerPage />)

    expect(await screen.findByText('Unknown (#999)')).toBeInTheDocument()
  })

  it('renders dynamic custom-field columns from the Metadata Registry', async () => {
    mockFetchRouter({
      '/api/v1/metadata/custom-fields': () =>
        jsonResponse([{ id: 7, name: 'Reference interne', data_type: 'string', extra_data: {} }]),
      '/api/v1/documents': () =>
        jsonResponse(
          makePage({
            items: [
              {
                id: 1,
                title: 'Doc with a custom field',
                correspondent: null,
                document_type: null,
                tags: [],
                created: null,
                modified: null,
                added: null,
                archive_serial_number: null,
                custom_fields: [
                  {
                    field_id: 7,
                    kind: 'present',
                    raw: 'REF-42',
                    monetary: null,
                    select_option_id: null,
                    select_label: null,
                  },
                ],
                user_can_change: null,
              },
            ],
            total: 1,
            page_count: 1,
          }),
        ),
    })
    renderWithProviders(<ExplorerPage />)

    expect(await screen.findByText('Reference interne')).toBeInTheDocument()
    expect(screen.getByText('REF-42')).toBeInTheDocument()
  })

  it('select-one and select-current-page both update the selection summary', async () => {
    mockFetchRouter()
    renderWithProviders(<ExplorerPage />)
    await screen.findByText('Invoice #1')

    const user = userEvent.setup()
    const rowCheckbox = screen.getAllByLabelText('Select row', { selector: 'input' })[0]
    if (rowCheckbox === undefined) {
      throw new Error('expected at least one row checkbox')
    }
    await user.click(rowCheckbox)

    expect(await screen.findByText('1 selected')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Clear selection' }))
    expect(screen.queryByText('1 selected')).not.toBeInTheDocument()

    await user.click(screen.getByLabelText('Select all on this page'))
    expect(await screen.findByText('2 selected')).toBeInTheDocument()
  })

  it('never issues a PATCH, POST, PUT or DELETE request while browsing', async () => {
    const spy = mockFetchRouter()
    renderWithProviders(<ExplorerPage />)
    await screen.findByText('Invoice #1')

    const user = userEvent.setup()
    await user.click(screen.getByLabelText('Select all on this page'))
    await user.click(screen.getByRole('button', { name: 'Columns' }))

    for (const call of spy.mock.calls) {
      const init = call[1] as RequestInit | undefined
      const method = (init?.method ?? 'GET').toUpperCase()
      expect(['GET', 'HEAD']).toContain(method)
    }
  })

  it('sends the requested page size to the backend when changed', async () => {
    const spy = mockFetchRouter()
    renderWithProviders(<ExplorerPage />)
    await screen.findByText('Invoice #1')

    const user = userEvent.setup()
    const pageSizeSelect = screen.getByLabelText('Rows per page', {
      selector: 'select',
    })
    await user.selectOptions(pageSizeSelect, '25')

    await waitFor(() => {
      const documentCalls = spy.mock.calls.filter((call) =>
        String(call[0]).startsWith('/api/v1/documents'),
      )
      const last = documentCalls[documentCalls.length - 1]
      expect(String(last?.[0])).toContain('page_size=25')
    })
  })

  it('sorting a sortable column sends the corresponding ordering parameter', async () => {
    const spy = mockFetchRouter()
    renderWithProviders(<ExplorerPage />)
    await screen.findByText('Invoice #1')

    const user = userEvent.setup()
    await user.click(screen.getByText('Title'))

    await waitFor(() => {
      const documentCalls = spy.mock.calls.filter((call) =>
        String(call[0]).startsWith('/api/v1/documents'),
      )
      const last = documentCalls[documentCalls.length - 1]
      expect(String(last?.[0])).toContain('ordering=title')
    })
  })
})
