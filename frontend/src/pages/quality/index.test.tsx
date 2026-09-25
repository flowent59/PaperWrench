import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { DocumentSchema, QualityPage } from '@/api/types'

import { QualityPageView } from './index'
import { observed, expected } from './format'
import { exactIdsHref, exactQueryHref, qualityLocation } from './navigation'

const schema: DocumentSchema = {
  id: 4, name: 'Vacations', description: null, applies_when: {},
  rules: [{ kind: 'required', field: { source: 'custom_field', field_id: 2 }, field_type: 'monetary' }],
  created_at: '', updated_at: '',
}

const page: QualityPage = {
  schema_id: 4, schema_name: 'Vacations', page: 1, page_size: 25,
  page_count: 2, dataset_total: 30, evaluated_count: 2, violation_count: 1,
  items: [{ document_id: 7, title: 'Missing amount', rule: {
    rule_index: 0, field: { source: 'custom_field', field_id: 2, display_name: 'Montant' },
    kind: 'required', status: 'fail', value_kind: 'absent', actual: null,
    expected: null, code: 'REQUIRED',
  } }],
  rules: [{ rule_index: 0, violation_count: 1,
    exact_query: { filters: { root: { kind: 'group', operator: 'and', children: [
      { kind: 'condition', field: { source: 'custom_field', field_id: 2 }, operator: 'is_missing' },
    ] } } },
    page_document_ids: [7] }],
}

afterEach(() => vi.unstubAllGlobals())

describe('Quality', () => {
  it('shows separate counters, details and stable exact Explorer links', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => new Response(
      JSON.stringify(String(input).includes('/quality/') ? page : [schema]),
      { status: 200, headers: { 'Content-Type': 'application/json' } },
    )))
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={queryClient}><MemoryRouter><QualityPageView /></MemoryRouter></QueryClientProvider>)
    expect(await screen.findByText('Missing amount (#7)')).toBeInTheDocument()
    expect(screen.getByText('Documents evaluated on this page').previousSibling).toHaveTextContent('2')
    expect(screen.getByText('Violations on this page').previousSibling).toHaveTextContent('1')
    expect(screen.getByText('Exact dataset total in Paperless').previousSibling).toHaveTextContent('30')
    expect(screen.getByText('ABSENT')).toBeInTheDocument()
    expect(screen.getByText('Present, non-empty value')).toBeInTheDocument()
    expect(screen.getByText('Open exact violation condition in Explorer').closest('a')).toHaveAttribute(
      'href', exactQueryHref(page.rules[0]!.exact_query!),
    )
    expect(screen.getByText('Open document').closest('a')).toHaveAttribute('href', exactIdsHref([7]))
  })

  it('uses only IDs when the complement is unavailable', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => new Response(
      JSON.stringify(String(input).includes('/quality/') ? { ...page, rules: [{
        ...page.rules[0]!, exact_query: null,
      }] } : [schema]), { status: 200, headers: { 'Content-Type': 'application/json' } },
    )))
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={queryClient}><MemoryRouter><QualityPageView /></MemoryRouter></QueryClientProvider>)
    expect(await screen.findByText('Open exact IDs from this page in Explorer')).toHaveAttribute(
      'href', '/documents?quality_ids=7',
    )
  })

  it('preserves observed states, decimals, booleans and select IDs', () => {
    const rule = page.items[0]!.rule
    expect(observed(rule)).toBe('ABSENT')
    expect(observed({ ...rule, value_kind: 'null' })).toBe('NULL')
    expect(observed({ ...rule, value_kind: 'present', actual: '' })).toBe('""')
    expect(observed({ ...rule, value_kind: 'present', actual: 0 })).toBe('0')
    expect(observed({ ...rule, value_kind: 'present', actual: false })).toBe('false')
    expect(observed({ ...rule, value_kind: 'present', actual: '0.00' })).toBe('"0.00"')
    expect(observed({ ...rule, value_kind: 'present', actual: 'option-1' })).toBe('"option-1"')
    expect(expected({ ...rule, kind: 'equals', expected: false })).toBe('false')
  })

  it('round-trips exact query and IDs without treating invalid IDs as a selection', () => {
    const query = { search: { mode: 'title' as const, text: 'vacation' }, filters: {
      root: { kind: 'group' as const, operator: 'and' as const, children: [
        { kind: 'condition' as const, field: { source: 'custom_field' as const, field_id: 2 },
          operator: 'is_missing' as const },
      ] },
    } }
    expect(qualityLocation(exactQueryHref(query).split('?')[1] ?? '').query?.search?.text).toBe('vacation')
    expect(qualityLocation('?quality_ids=7,3,7').ids).toEqual([7, 3])
    expect(qualityLocation('?quality_ids=7,nope').ids).toBeNull()
    expect(qualityLocation('?quality_ids=7,nope').invalid).toBe(true)
    expect(qualityLocation('?quality_query=%7B%7D').invalid).toBe(true)
  })
})
