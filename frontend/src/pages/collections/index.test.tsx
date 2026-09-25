import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'

import { CollectionPage, CollectionsPage } from './index'

afterEach(() => vi.unstubAllGlobals())

it('shows an empty list and creates a named collection', async () => {
  const names: string[] = []
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    expect(String(input)).toBe('/api/v1/collections')
    if (init?.method === 'POST') {
      names.push((JSON.parse(String(init.body)) as { name: string }).name)
    }
    const rows = names.map((name, index) => ({ id: index + 1, name,
      description: null, member_count: 0, created_at: '', updated_at: '' }))
    return Promise.resolve(new Response(JSON.stringify(init?.method === 'POST' ? rows[0] : rows), {
      status: init?.method === 'POST' ? 201 : 200,
      headers: { 'Content-Type': 'application/json' },
    }))
  }))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><MemoryRouter><CollectionsPage /></MemoryRouter></QueryClientProvider>)
  expect(await screen.findByText('No collections yet.')).toBeInTheDocument()
  const user = userEvent.setup()
  await user.type(screen.getByLabelText('Name'), 'Vacations')
  await user.click(screen.getByRole('button', { name: 'Create collection' }))
  await waitFor(() => expect(names).toEqual(['Vacations']))
  expect(await screen.findByRole('link', { name: 'Vacations' })).toBeInTheDocument()
})

it('shows a collection list error', async () => {
  vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response(JSON.stringify({
    error: { code: 'TEST', message: 'Unavailable' },
  }), { status: 503, headers: { 'Content-Type': 'application/json' } }))))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><MemoryRouter><CollectionsPage /></MemoryRouter></QueryClientProvider>)
  expect(await screen.findByRole('alert')).toHaveTextContent('Collection request failed.')
})

it('reloads exact membership, hides unavailable metadata and removes a member', async () => {
  const removed: number[] = []
  const spy = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input).split('?')[0]
    let body: unknown
    if (path === '/api/v1/collections') {
      body = [{ id: 7, name: 'Vacations', description: null, member_count: 2,
        created_at: '', updated_at: '' }]
    } else if (path === '/api/v1/collections/7/documents' && init?.method === 'DELETE') {
      removed.push(...(JSON.parse(String(init.body)) as { document_ids: number[] }).document_ids)
      body = { id: 7, name: 'Vacations', description: null, member_count: 1,
        created_at: '', updated_at: '' }
    } else if (path === '/api/v1/collections/7/documents') {
      body = { page: 1, page_size: 25, page_count: 1, total: 2, items: [
        { document_id: 1, available: true, document: { id: 1, title: 'Vacation 1' } },
        { document_id: 99, available: false, document: null },
      ] }
    } else { throw new Error(`Unexpected ${path}`) }
    return Promise.resolve(new Response(JSON.stringify(body), {
      status: 200, headers: { 'Content-Type': 'application/json' },
    }))
  })
  vi.stubGlobal('fetch', spy)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/collections/7']}>
    <Routes><Route path="/collections" element={<CollectionsPage />} />
      <Route path="/collections/:collectionId" element={<CollectionPage />} /></Routes>
  </MemoryRouter></QueryClientProvider>)
  expect(await screen.findByText('#1 · Vacation 1')).toBeInTheDocument()
  expect(screen.getByText('#99 · Unavailable document')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: '#1 · Vacation 1' })).toHaveAttribute('href', '/documents/1')
  const user = userEvent.setup()
  await user.click(screen.getAllByRole('button', { name: 'Remove member' })[1]!)
  await waitFor(() => expect(removed).toEqual([99]))
})
