import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { DocumentSchema, FilterCapabilities } from '@/api/types'

import { SchemasPage } from './index'

const capabilities: FilterCapabilities = {
  fields: [
    { key: 'custom_field:1', label: 'Période concernée', field_type: 'text',
      source: 'custom_field', custom_field_id: 1, reference_kind: null, select_options: [],
      operators: [{ operator: 'equals', label: 'equals', value_shape: 'text', multi: false, note: null }] },
    { key: 'custom_field:2', label: 'Montant', field_type: 'monetary',
      source: 'custom_field', custom_field_id: 2, reference_kind: null, select_options: [],
      operators: [{ operator: 'equals', label: 'equals', value_shape: 'decimal', multi: false, note: null }] },
    { key: 'custom_field:3', label: 'Catégorie', field_type: 'select',
      source: 'custom_field', custom_field_id: 3, reference_kind: null,
      select_options: [{ id: 'option-1', label: 'Approuvé' }],
      operators: [{ operator: 'equals', label: 'equals', value_shape: 'select_option', multi: false, note: null }] },
  ],
  grouping: { and_supported: true, or_custom_fields_supported: true, or_core_fields_supported: false,
    or_mixed_supported: false, not_supported: false, max_conditions: 50, max_depth: 10,
    custom_field_max_depth: 10, custom_field_max_conditions: 20 },
  search_modes: [], operator_semantics: {},
}

const saved: DocumentSchema = {
  id: 8, name: 'Relevé de vacations', description: null,
  applies_when: { filters: { root: { kind: 'group', operator: 'and', children: [] } } },
  rules: [{ kind: 'required', field: { source: 'custom_field', field_id: 2 }, field_type: 'monetary' }],
  created_at: '2026-09-25T00:00:00Z', updated_at: '2026-09-25T00:00:00Z',
}

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function setup(initial: DocumentSchema[] = [], invalid = false) {
  const calls: Array<{ path: string; init: RequestInit | undefined }> = []
  let schemas = initial
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input)
    calls.push({ path, init })
    if (path.endsWith('/schemas') && init?.method === 'POST') {
      const next = { ...JSON.parse(String(init.body)), id: 8,
        created_at: saved.created_at, updated_at: saved.updated_at } as DocumentSchema
      schemas = [next]
      return response(next, 201)
    }
    if (path.endsWith('/schemas/8') && init?.method === 'PUT') {
      const next = { ...saved, ...JSON.parse(String(init.body)) } as DocumentSchema
      schemas = [next]
      return response(next)
    }
    if (path.endsWith('/schemas')) return response(schemas)
    if (path.endsWith('/filters/capabilities')) return response(capabilities)
    if (path.endsWith('/filters/validate')) return response(invalid
      ? { valid: true, compilable: false, issues: [{ stage: 'compilation',
        code: 'CORE_OR_UNSUPPORTED', path: 'root', message: 'Cannot compile scope' }] }
      : { valid: true, compilable: true, issues: [] })
    if (path.includes('/schemas/8/evaluate')) return response({ schema_id: 8, page: 1,
      page_size: 25, total: 2, page_count: 1, items: [
        { document_id: 1, title: 'Zero', status: 'pass', rules: [{ rule_index: 0,
          field: { source: 'custom_field', field_id: 2 }, kind: 'required', status: 'pass',
          value_kind: 'present', actual: '0.00', expected: null, code: null }] },
        { document_id: 2, title: 'Absent', status: 'fail', rules: [{ rule_index: 0,
          field: { source: 'custom_field', field_id: 2 }, kind: 'required', status: 'fail',
          value_kind: 'absent', actual: null, expected: null, code: 'REQUIRED' }] },
      ] })
    if (path.includes('/metadata/')) return response([])
    throw new Error(`Unexpected request: ${path}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={queryClient}><MemoryRouter><SchemasPage /></MemoryRouter></QueryClientProvider>)
  return calls
}

afterEach(() => vi.unstubAllGlobals())

describe('M10 schema editor', () => {
  it('creates typed required and equals rules using field and option identities', async () => {
    const calls = setup()
    await screen.findByRole('button', { name: 'Add rule' })
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Relevé de vacations' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add rule' }))
    fireEvent.change(screen.getByLabelText('Rule 1 field'), { target: { value: 'custom_field:2' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add rule' }))
    fireEvent.change(screen.getByLabelText('Rule 2 field'), { target: { value: 'custom_field:3' } })
    fireEvent.change(screen.getByLabelText('Rule 2 kind'), { target: { value: 'equals' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save schema' }))
    await waitFor(() => expect(calls.some(call => call.init?.method === 'POST')).toBe(true))
    const body = JSON.parse(String(calls.find(call => call.init?.method === 'POST')?.init?.body))
    expect(body.rules).toEqual([
      { kind: 'required', field: { source: 'custom_field', field_id: 2,
        display_name: 'Montant' }, field_type: 'monetary' },
      { kind: 'equals', field: { source: 'custom_field', field_id: 3,
        display_name: 'Catégorie' }, field_type: 'select', value: 'option-1' },
    ])
  })

  it('loads a schema, edits it and renders per-document conformance', async () => {
    const calls = setup([saved])
    fireEvent.click(await screen.findByRole('button', { name: saved.name }))
    fireEvent.change(screen.getByLabelText('Description'), { target: { value: 'Monthly checks' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save schema' }))
    await waitFor(() => expect(calls.some(call => call.init?.method === 'PUT')).toBe(true))
    expect(JSON.parse(String(calls.find(call => call.init?.method === 'PUT')?.init?.body)).description)
      .toBe('Monthly checks')
    fireEvent.click(screen.getByRole('button', { name: 'Evaluate' }))
    await screen.findByText('Zero')
    expect(screen.getByText('Absent')).toBeInTheDocument()
    expect(screen.getByText(/REQUIRED/)).toBeInTheDocument()
  })

  it('shows server compilation errors and disables save', async () => {
    const scoped = { ...saved, applies_when: { filters: { root: { kind: 'group' as const,
      operator: 'and' as const, children: [{ kind: 'condition' as const,
        field: { source: 'custom_field' as const, field_id: 1 }, operator: 'equals' as const,
        value: 'September' }] } } } }
    setup([scoped], true)
    fireEvent.click(await screen.findByRole('button', { name: scoped.name }))
    await screen.findByText('The scope cannot be compiled. Fix its filter before saving.')
    expect(screen.getByRole('button', { name: 'Save schema' })).toBeDisabled()
  })
})
