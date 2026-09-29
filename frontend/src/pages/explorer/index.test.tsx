import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { DocumentPage, FilterCapabilities } from '@/api/types'

import { ExplorerPage } from './index'
import { exactQueryHref } from '@/pages/quality/navigation'

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

function renderWithProviders(children: React.ReactNode, initialEntry = '/') {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}><MemoryRouter initialEntries={[initialEntry]}>{children}</MemoryRouter></QueryClientProvider>,
  )
}

/**
 * A minimal capabilities payload.
 *
 * Deliberately not the real one: these tests are about the Explorer, and
 * hardcoding a full capability matrix here would make them fail whenever the
 * backend legitimately gained a field. Two fields is enough to prove the
 * builder renders what it is given.
 */
function makeCapabilities(
  overrides: Partial<FilterCapabilities> = {},
): FilterCapabilities {
  return {
    fields: [
      {
        key: 'core:title',
        label: 'Title',
        field_type: 'text',
        source: 'core',
        custom_field_id: null,
        reference_kind: null,
        select_options: [],
        operators: [
          {
            operator: 'contains',
            label: 'contains',
            value_shape: 'text',
            multi: false,
            note: 'Case-insensitive substring match.',
          },
        ],
      },
      {
        key: 'custom_field:2',
        label: 'Montant',
        field_type: 'monetary',
        source: 'custom_field',
        custom_field_id: 2,
        reference_kind: null,
        select_options: [],
        operators: [
          {
            operator: 'greater_than',
            label: 'is greater than',
            value_shape: 'decimal',
            multi: false,
            note: null,
          },
          {
            operator: 'is_missing',
            label: 'is missing',
            value_shape: 'none',
            multi: false,
            note: null,
          },
        ],
      },
    ],
    grouping: {
      and_supported: true,
      or_custom_fields_supported: true,
      or_core_fields_supported: false,
      or_mixed_supported: false,
      not_supported: false,
      max_conditions: 50,
      max_depth: 10,
      custom_field_max_depth: 10,
      custom_field_max_conditions: 20,
    },
    search_modes: [
      { mode: 'title', label: 'Title', description: 'Search titles only.' },
      { mode: 'content', label: 'Content', description: 'Search extracted text.' },
      { mode: 'advanced', label: 'Advanced query', description: 'Raw Paperless syntax.' },
    ],
    operator_semantics: {},
    ...overrides,
  }
}

/** The request bodies sent to a given path, parsed. */
function bodiesFor(spy: ReturnType<typeof vi.fn>, path: string): Record<string, unknown>[] {
  return spy.mock.calls
    .filter((call) => String(call[0]) === path)
    .map((call) => {
      const init = call[1] as RequestInit | undefined
      return JSON.parse(String(init?.body ?? '{}')) as Record<string, unknown>
    })
}

/** Routes a mocked fetch by URL path, so each test only wires what it needs. */
function mockFetchRouter(
  handlers: Record<string, (init?: RequestInit) => Response> = {},
): ReturnType<typeof vi.fn> {
  const defaultHandlers: Record<string, (init?: RequestInit) => Response> = {
    '/api/v1/collections': () => jsonResponse([]),
    '/api/v1/metadata/tags': () => jsonResponse([]),
    '/api/v1/metadata/correspondents': () => jsonResponse([]),
    '/api/v1/metadata/document-types': () => jsonResponse([]),
    '/api/v1/metadata/storage-paths': () => jsonResponse([]),
    '/api/v1/metadata/custom-fields': () => jsonResponse([]),
    '/api/v1/filters/capabilities': () => jsonResponse(makeCapabilities()),
    '/api/v1/filters/validate': () =>
      jsonResponse({ valid: true, compilable: true, issues: [], compiled: { params: {} } }),
    '/api/v1/filters/count': () =>
      jsonResponse({ count: 2, compiled: { params: {} } }),
    '/api/v1/documents/query': () => jsonResponse(makePage()),
  }
  const merged = { ...defaultHandlers, ...handlers }

  const spy = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input.toString()
    const path = url.split('?')[0] ?? url
    const handler = merged[path]
    if (handler === undefined) {
      throw new Error(`Unhandled fetch in test: ${url}`)
    }
    return Promise.resolve(handler(init))
  })
  vi.stubGlobal('fetch', spy)
  return spy
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('ExplorerPage', () => {
  it('refuses malformed Quality URLs without widening to the normal dataset', () => {
    const spy = mockFetchRouter()
    renderWithProviders(<ExplorerPage />, '/documents?quality_ids=1,not-an-id')
    expect(screen.getByRole('alert')).toHaveTextContent('Invalid Quality drill-down')
    expect(bodiesFor(spy, '/api/v1/documents/query')).toEqual([])
    expect(bodiesFor(spy, '/api/v1/documents/by-ids')).toEqual([])
  })

  it('opens only explicit Quality IDs and reports unavailable documents', async () => {
    const spy = mockFetchRouter({
      '/api/v1/documents/by-ids': () => jsonResponse({ ...makePage({
        items: [makePage().items[0]!], total: 1,
      }), unavailable_count: 1 }),
    })
    renderWithProviders(<ExplorerPage />, '/documents?quality_ids=1,999')
    expect(await screen.findByText('Invoice #1')).toBeInTheDocument()
    expect(screen.getByText('1 document is unavailable.')).toBeInTheDocument()
    expect(bodiesFor(spy, '/api/v1/documents/by-ids')).toEqual([{ document_ids: [1, 999] }])
    expect(bodiesFor(spy, '/api/v1/documents/query')).toEqual([])
  })

  it('passes the exact compiled Quality query to the normal Explorer path', async () => {
    const query = { filters: { root: { kind: 'group' as const, operator: 'and' as const,
      children: [{ kind: 'condition' as const, field: { source: 'custom_field' as const, field_id: 2 },
        operator: 'is_missing' as const }] } } }
    const spy = mockFetchRouter()
    renderWithProviders(<ExplorerPage />, exactQueryHref(query))
    expect(await screen.findByText('Invoice #1')).toBeInTheDocument()
    expect(bodiesFor(spy, '/api/v1/documents/query')[0]?.filters).toEqual(query.filters)
    expect(bodiesFor(spy, '/api/v1/documents/by-ids')).toEqual([])
  })

  it('renders the loading state before data arrives', () => {
    mockFetchRouter({
      '/api/v1/documents/query': () => jsonResponse(makePage()),
    })
    renderWithProviders(<ExplorerPage />)

    expect(screen.getByText('Loading documents...')).toBeInTheDocument()
  })

  it('renders document rows once loaded', async () => {
    mockFetchRouter()
    renderWithProviders(<ExplorerPage />)

    expect(await screen.findByText('Invoice #1')).toBeInTheDocument()
    expect(screen.getByText('Invoice #2')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Invoice #1' })).toHaveAttribute('href', '/documents/1')
    expect(screen.getByText('Acme Corp')).toBeInTheDocument()
    expect(screen.getByText('Important')).toBeInTheDocument()
  })

  it('renders the empty state when no documents match', async () => {
    mockFetchRouter({
      '/api/v1/documents/query': () =>
        jsonResponse(makePage({ items: [], total: 0, page_count: 0 })),
    })
    renderWithProviders(<ExplorerPage />)

    expect(await screen.findByText('No documents')).toBeInTheDocument()
  })

  it('renders the error state and allows retrying', async () => {
    let calls = 0
    mockFetchRouter({
      '/api/v1/documents/query': () => {
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
      '/api/v1/documents/query': () =>
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
      '/api/v1/documents/query': () =>
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

  it('saves exact IDs selected on two Explorer pages', async () => {
    let saved: number[] = []
    const spy = mockFetchRouter({
      '/api/v1/documents/query': (init) => {
        const body = JSON.parse(String(init?.body)) as { page: number }
        const first = makePage({ total: 4, page_count: 2 })
        return jsonResponse(body.page === 1 ? first : {
          ...first, page: 2, items: first.items.map(item => ({
            ...item, id: item.id + 2, title: `Vacation ${item.id + 2}`,
          })),
        })
      },
      '/api/v1/collections': (init) => {
        if (init?.method === 'POST') {
          saved = (JSON.parse(String(init.body)) as { document_ids: number[] }).document_ids
          return jsonResponse({ id: 1, name: 'Vacations', description: null,
            member_count: saved.length, created_at: '', updated_at: '' }, 201)
        }
        return jsonResponse([])
      },
    })
    renderWithProviders(<ExplorerPage />)
    await screen.findByText('Invoice #1')
    const user = userEvent.setup()
    await user.click(screen.getAllByLabelText('Select row', { selector: 'input' })[0]!)
    await user.click(screen.getByLabelText('Next page'))
    const secondPageTitle = await screen.findByText('Vacation 4', {}, { timeout: 5000 })
    const secondPageRow = secondPageTitle.closest('tr')
    if (secondPageRow === null) throw new Error('expected the second-page document row')
    await user.click(within(secondPageRow).getByLabelText('Select row', { selector: 'input' }))
    expect(screen.getByText('2 selected')).toBeInTheDocument()
    await user.type(screen.getByLabelText('Name'), 'Vacations')
    await user.click(screen.getByRole('button', { name: 'Save selected IDs' }))
    await waitFor(() => expect(saved).toEqual([1, 4]))
    expect(spy).toHaveBeenCalled()
  })

  it('adds selected IDs to an existing collection', async () => {
    let added: number[] = []
    mockFetchRouter({
      '/api/v1/collections': () => jsonResponse([{ id: 7, name: 'Vacations',
        description: null, member_count: 0, created_at: '', updated_at: '' }]),
      '/api/v1/collections/7/documents': (init) => {
        added = (JSON.parse(String(init?.body)) as { document_ids: number[] }).document_ids
        return jsonResponse({ id: 7, name: 'Vacations', member_count: added.length })
      },
    })
    renderWithProviders(<ExplorerPage />)
    await screen.findByText('Invoice #1')
    const user = userEvent.setup()
    await user.click(screen.getAllByLabelText('Select row', { selector: 'input' })[0]!)
    await user.selectOptions(screen.getByLabelText('Collection'), '7')
    await user.click(screen.getByRole('button', { name: 'Save selected IDs' }))
    await waitFor(() => expect(added).toEqual([1]))
  })

  it('never issues a mutating request while browsing', async () => {
    // M4 made the documents endpoint a POST - a *read* expressed as a POST,
    // because a filter tree does not belong in a query string. So the guard
    // can no longer be "only GET": it is now an allowlist of the three
    // read-only POST endpoints, and nothing else may be posted to at all.
    const READ_ONLY_POSTS = [
      '/api/v1/documents/query',
      '/api/v1/filters/validate',
      '/api/v1/filters/count',
    ]
    const spy = mockFetchRouter()
    renderWithProviders(<ExplorerPage />)
    await screen.findByText('Invoice #1')

    const user = userEvent.setup()
    await user.click(screen.getByLabelText('Select all on this page'))
    await user.click(screen.getByRole('button', { name: 'Columns' }))
    await user.click(screen.getByRole('button', { name: /Filters/ }))

    for (const call of spy.mock.calls) {
      const init = call[1] as RequestInit | undefined
      const method = (init?.method ?? 'GET').toUpperCase()
      const path = String(call[0]).split('?')[0]
      expect(['GET', 'HEAD', 'POST']).toContain(method)
      if (method === 'POST') {
        expect(READ_ONLY_POSTS).toContain(path)
      }
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
      const bodies = bodiesFor(spy, '/api/v1/documents/query')
      expect(bodies[bodies.length - 1]?.page_size).toBe(25)
    })
  })

  it('sorting a sortable column sends the corresponding ordering parameter', async () => {
    const spy = mockFetchRouter()
    renderWithProviders(<ExplorerPage />)
    await screen.findByText('Invoice #1')

    const user = userEvent.setup()
    // Scoped to the grid header: "Title" is also a search-mode option now.
    await user.click(screen.getByRole('columnheader', { name: /Title/ }))

    await waitFor(() => {
      const bodies = bodiesFor(spy, '/api/v1/documents/query')
      expect(bodies[bodies.length - 1]?.ordering).toBe('title')
    })
  })
})

describe('ExplorerPage - Filter Builder (M4)', () => {
  async function openFilters() {
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: /Filters/ }))
    return user
  }

  it('offers only the fields the backend says exist', async () => {
    // No hardcoded field names anywhere: custom fields appear because the
    // capabilities endpoint reported them.
    mockFetchRouter()
    renderWithProviders(<ExplorerPage />)
    await screen.findByText('Invoice #1')

    const user = await openFilters()
    await user.click(screen.getByRole('button', { name: 'Add condition' }))

    const fieldSelect = await screen.findByLabelText('Field', { selector: 'select' })
    const labels = Array.from(fieldSelect.querySelectorAll('option')).map(
      (option) => option.textContent,
    )
    expect(labels).toEqual(['Title', 'Montant'])
  })

  it('offers only the operators that field type allows', async () => {
    mockFetchRouter()
    renderWithProviders(<ExplorerPage />)
    await screen.findByText('Invoice #1')

    const user = await openFilters()
    await user.click(screen.getByRole('button', { name: 'Add condition' }))
    await user.selectOptions(
      await screen.findByLabelText('Field', { selector: 'select' }),
      'custom_field:2',
    )

    const operatorSelect = screen.getByLabelText('Condition', { selector: 'select' })
    const operators = Array.from(operatorSelect.querySelectorAll('option')).map(
      (option) => option.textContent,
    )
    // A monetary field has comparisons and the missing family, and no
    // `contains` - because Paperless's own table does not allow one.
    expect(operators).toEqual(['is greater than', 'is missing'])
  })

  it('shows the operator caveat the backend attached', async () => {
    // "Equals is case-insensitive" is a Paperless behaviour, worded by the
    // backend so there is only one copy of it.
    mockFetchRouter()
    renderWithProviders(<ExplorerPage />)
    await screen.findByText('Invoice #1')

    const user = await openFilters()
    await user.click(screen.getByRole('button', { name: 'Add condition' }))

    expect(
      await screen.findByText('Case-insensitive substring match.'),
    ).toBeInTheDocument()
  })

  it('hides the value input for an operator that takes no value', async () => {
    mockFetchRouter()
    renderWithProviders(<ExplorerPage />)
    await screen.findByText('Invoice #1')

    const user = await openFilters()
    await user.click(screen.getByRole('button', { name: 'Add condition' }))
    await user.selectOptions(
      await screen.findByLabelText('Field', { selector: 'select' }),
      'custom_field:2',
    )

    expect(screen.getByLabelText('Montant is greater than')).toBeInTheDocument()

    await user.selectOptions(
      screen.getByLabelText('Condition', { selector: 'select' }),
      'is_missing',
    )
    expect(screen.queryByLabelText('Montant is missing')).not.toBeInTheDocument()
  })

  it('sends the built FilterSet in the documents request', async () => {
    const spy = mockFetchRouter()
    renderWithProviders(<ExplorerPage />)
    await screen.findByText('Invoice #1')

    const user = await openFilters()
    await user.click(screen.getByRole('button', { name: 'Add condition' }))
    await user.selectOptions(
      await screen.findByLabelText('Field', { selector: 'select' }),
      'custom_field:2',
    )
    await user.selectOptions(
      screen.getByLabelText('Condition', { selector: 'select' }),
      'is_missing',
    )

    await waitFor(() => {
      const bodies = bodiesFor(spy, '/api/v1/documents/query')
      const last = bodies[bodies.length - 1] as {
        filters?: { root: { children: unknown[] } }
      }
      expect(last.filters?.root.children).toEqual([
        {
          kind: 'condition',
          field: { source: 'custom_field', field_id: 2, display_name: 'Montant' },
          operator: 'is_missing',
          value: null,
        },
      ])
    })
  })

  it('asks the backend to validate every change', async () => {
    // The builder constrains what can be expressed, but it is not the
    // authority - this is.
    const spy = mockFetchRouter()
    renderWithProviders(<ExplorerPage />)
    await screen.findByText('Invoice #1')

    const user = await openFilters()
    await user.click(screen.getByRole('button', { name: 'Add condition' }))

    await waitFor(() => {
      expect(bodiesFor(spy, '/api/v1/filters/validate')).not.toHaveLength(0)
    })
  })

  it('shows the Filter Engine count next to the results', async () => {
    mockFetchRouter({
      '/api/v1/filters/count': () =>
        jsonResponse({ count: 1438, compiled: { params: {} } }),
    })
    renderWithProviders(<ExplorerPage />)
    await screen.findByText('Invoice #1')

    const user = await openFilters()
    await user.click(screen.getByRole('button', { name: 'Add condition' }))

    expect(await screen.findByText('1438 documents match')).toBeInTheDocument()
  })

  it('reports "valid but not compilable" differently from an invalid filter', async () => {
    // Not a user error: Paperless simply cannot express the question, and
    // PaperWrench will not run an approximation of it.
    mockFetchRouter({
      '/api/v1/filters/validate': () =>
        jsonResponse({
          valid: true,
          compilable: false,
          issues: [
            {
              stage: 'compilation',
              code: 'MIXED_OR_UNSUPPORTED',
              path: 'root',
              message: 'Paperless cannot express OR between a core field and a custom field.',
              field: null,
              operator: null,
              details: null,
            },
          ],
          compiled: null,
        }),
    })
    renderWithProviders(<ExplorerPage />)
    await screen.findByText('Invoice #1')

    const user = await openFilters()
    await user.click(screen.getByRole('button', { name: 'Add condition' }))

    // Said in two places on purpose: the verdict line under the builder, and
    // in place of the grid - so a stale result set is never left on screen
    // looking like it belongs to the filter above it.
    expect(
      await screen.findAllByText('Paperless cannot run this filter'),
    ).toHaveLength(2)
    expect(
      screen.getByText(
        'Paperless-ngx cannot combine these core and custom fields with OR.',
      ),
    ).toBeInTheDocument()
    expect(screen.queryByText('Filter is not valid')).not.toBeInTheDocument()
  })

  it('does not fetch documents while the filter is not compilable', async () => {
    // The grid must never show rows produced by a different filter than the
    // one on screen.
    const spy = mockFetchRouter({
      '/api/v1/filters/validate': () =>
        jsonResponse({
          valid: true,
          compilable: false,
          issues: [
            {
              stage: 'compilation',
              code: 'MIXED_OR_UNSUPPORTED',
              path: 'root',
              message: 'Not expressible.',
              field: null,
              operator: null,
              details: null,
            },
          ],
          compiled: null,
        }),
    })
    renderWithProviders(<ExplorerPage />)
    await screen.findByText('Invoice #1')

    const user = await openFilters()
    await user.click(screen.getByRole('button', { name: 'Add condition' }))
    await screen.findAllByText('Paperless cannot run this filter')

    // The strong form of the assertion: no documents request ever carried
    // the filter, so no page of rows can have come from it.
    for (const body of bodiesFor(spy, '/api/v1/documents/query')) {
      expect(body.filters).toBeUndefined()
    }
    expect(bodiesFor(spy, '/api/v1/filters/count')).toHaveLength(0)
  })

  it('shows an inline issue against the row that caused it', async () => {
    mockFetchRouter({
      '/api/v1/filters/validate': () =>
        jsonResponse({
          valid: false,
          compilable: false,
          issues: [
            {
              stage: 'validation',
              code: 'VALUE_REQUIRED',
              path: 'root.children[0].value',
              message: "'contains' requires a value.",
              field: 'core:title',
              operator: 'contains',
              details: null,
            },
          ],
          compiled: null,
        }),
    })
    renderWithProviders(<ExplorerPage />)
    await screen.findByText('Invoice #1')

    const user = await openFilters()
    await user.click(screen.getByRole('button', { name: 'Add condition' }))

    expect(await screen.findByText('Enter a value for this condition.')).toBeInTheDocument()
    expect(screen.getByText('Filter is not valid')).toBeInTheDocument()
  })

  it('offers an OR group only for custom fields', async () => {
    mockFetchRouter()
    renderWithProviders(<ExplorerPage />)
    await screen.findByText('Invoice #1')

    const user = await openFilters()
    await user.click(screen.getByRole('button', { name: 'Add "any of" group' }))

    const fieldSelects = await screen.findAllByLabelText('Field', { selector: 'select' })
    const labels = Array.from(
      (fieldSelects[0] as HTMLSelectElement).querySelectorAll('option'),
    ).map((option) => option.textContent)
    // Title is a core field, so it is not offered inside the group.
    expect(labels).toEqual(['Montant'])
  })

  it('hides the OR group button when the backend cannot compile one', async () => {
    mockFetchRouter({
      '/api/v1/filters/capabilities': () =>
        jsonResponse(
          makeCapabilities({
            grouping: {
              ...makeCapabilities().grouping,
              or_custom_fields_supported: false,
            },
          }),
        ),
    })
    renderWithProviders(<ExplorerPage />)
    await screen.findByText('Invoice #1')

    const user = await openFilters()
    await user.click(screen.getByRole('button', { name: 'Add condition' }))

    expect(
      screen.queryByRole('button', { name: 'Add "any of" group' }),
    ).not.toBeInTheDocument()
  })

  it('can show the query that will actually be sent to Paperless', async () => {
    mockFetchRouter({
      '/api/v1/filters/validate': () =>
        jsonResponse({
          valid: true,
          compilable: true,
          issues: [],
          compiled: {
            params: { custom_field_query: '[2,"exists",false]' },
            custom_field_expression: [2, 'exists', false],
          },
        }),
    })
    renderWithProviders(<ExplorerPage />)
    await screen.findByText('Invoice #1')

    const user = await openFilters()
    await user.click(screen.getByRole('button', { name: 'Add condition' }))
    await user.click(await screen.findByRole('button', { name: 'Show query' }))

    expect(
      await screen.findByText('custom_field_query=[2,"exists",false]'),
    ).toBeInTheDocument()
  })

  it('sends the chosen search mode rather than always searching titles', async () => {
    // M3 had one search box that always meant `title_search` and said so
    // nowhere. The three modes are three different Paperless indexes.
    const spy = mockFetchRouter()
    renderWithProviders(<ExplorerPage />)
    await screen.findByText('Invoice #1')

    const user = userEvent.setup()
    await user.type(screen.getByLabelText('Search by title...'), 'vacations')
    await user.selectOptions(screen.getByLabelText('Search in'), 'content')

    await waitFor(() => {
      const bodies = bodiesFor(spy, '/api/v1/documents/query')
      const last = bodies[bodies.length - 1] as { search?: { mode: string; text: string } }
      expect(last.search).toEqual({ mode: 'content', text: 'vacations' })
    })
  })

  it('resets to page 1 when the filter changes', async () => {
    // Page 7 of the old result set is not page 7 of the new one.
    const spy = mockFetchRouter({
      '/api/v1/documents/query': () =>
        jsonResponse(makePage({ total: 500, page_count: 5 })),
    })
    renderWithProviders(<ExplorerPage />)
    await screen.findByText('Invoice #1')

    const user = userEvent.setup()
    await user.click(screen.getByLabelText('Next page'))
    await waitFor(() => {
      const bodies = bodiesFor(spy, '/api/v1/documents/query')
      expect(bodies[bodies.length - 1]?.page).toBe(2)
    })

    await user.click(screen.getByRole('button', { name: /Filters/ }))
    await user.click(screen.getByRole('button', { name: 'Add condition' }))

    await waitFor(() => {
      const bodies = bodiesFor(spy, '/api/v1/documents/query')
      expect(bodies[bodies.length - 1]?.page).toBe(1)
    })
  })

  it('clears the filter and the search together', async () => {
    mockFetchRouter()
    renderWithProviders(<ExplorerPage />)
    await screen.findByText('Invoice #1')

    const user = await openFilters()
    await user.click(screen.getByRole('button', { name: 'Add condition' }))
    expect(await screen.findByLabelText('Field', { selector: 'select' })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Clear filters' }))

    expect(screen.queryByLabelText('Field', { selector: 'select' })).not.toBeInTheDocument()
    expect(screen.getByText('No filters. Every document matches.')).toBeInTheDocument()
  })

  it('never fetches every matching document to build the filter panel', async () => {
    // The count comes from the Filter Engine (which asks Paperless for its
    // own count) - it is never derived by paging through matches.
    const spy = mockFetchRouter({
      '/api/v1/filters/count': () =>
        jsonResponse({ count: 50000, compiled: { params: {} } }),
    })
    renderWithProviders(<ExplorerPage />)
    await screen.findByText('Invoice #1')

    const user = await openFilters()
    await user.click(screen.getByRole('button', { name: 'Add condition' }))
    await screen.findByText('50000 documents match')

    const pages = bodiesFor(spy, '/api/v1/documents/query')
    for (const body of pages) {
      expect([25, 50, 100, 250]).toContain(body.page_size)
    }
    expect(pages.length).toBeLessThan(6)
  })
})
