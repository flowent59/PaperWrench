/**
 * The Explorer - M3's headline deliverable.
 *
 * Read-only browsing of the Paperless-ngx library: server-side pagination,
 * server-side sorting (allowlisted on the backend), simple search, basic
 * direct metadata filters, column visibility, multi-selection persisted
 * across page navigation by document id, and dynamic custom-field columns
 * pulled live from the Metadata Registry.
 *
 * This component issues exactly one kind of request: GET. Nothing here
 * ever calls a mutating endpoint - the no-write guarantee is enforced by
 * `documentsApi`/`metadataApi` only exposing `GET`s in the first place.
 */

import {
  flexRender,
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
  type SortingState,
} from '@tanstack/react-table'
import { AlertTriangle, ChevronLeft, ChevronRight, Columns3, X } from 'lucide-react'
import * as React from 'react'

import {
  useCorrespondents,
  useCustomFields,
  useDocumentTypes,
  useDocuments,
  useTags,
} from '@/api/queries'
import type { DocumentListItem, DocumentPageSize, ListDocumentsParams } from '@/api/types'
import { DOCUMENT_PAGE_SIZES } from '@/api/types'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { messages } from '@/i18n/messages'
import { cn } from '@/lib/utils'

import { buildBaseColumns, buildCustomFieldColumns } from './columns'
import { usePersistedColumnVisibility } from './column-visibility'
import { useDocumentSelection } from './selection'

/** Maps a TanStack Table sort state to the single `ordering` key PaperWrench accepts. */
function sortingToOrdering(sorting: SortingState): string | undefined {
  const first = sorting[0]
  if (first === undefined) return undefined
  return first.desc ? `-${first.id}` : first.id
}

export function ExplorerPage() {
  const [page, setPage] = React.useState(1)
  const [pageSize, setPageSize] = React.useState<DocumentPageSize>(100)
  const [searchInput, setSearchInput] = React.useState('')
  const [search, setSearch] = React.useState('')
  const [sorting, setSorting] = React.useState<SortingState>([])
  const [documentType, setDocumentType] = React.useState<number | undefined>(undefined)
  const [correspondent, setCorrespondent] = React.useState<number | undefined>(undefined)
  const [tag, setTag] = React.useState<number | undefined>(undefined)
  const [columnVisibility, setColumnVisibility] = usePersistedColumnVisibility()
  const [columnsMenuOpen, setColumnsMenuOpen] = React.useState(false)
  const selection = useDocumentSelection()

  // Debounce the search box so every keystroke does not fire a request.
  React.useEffect(() => {
    const handle = window.setTimeout(() => {
      setSearch(searchInput)
      setPage(1)
    }, 350)
    return () => window.clearTimeout(handle)
  }, [searchInput])

  const ordering = sortingToOrdering(sorting)
  const params: ListDocumentsParams = {
    page,
    page_size: pageSize,
    ...(search ? { search } : {}),
    ...(ordering !== undefined ? { ordering } : {}),
    ...(documentType !== undefined ? { document_type: documentType } : {}),
    ...(correspondent !== undefined ? { correspondent } : {}),
    ...(tag !== undefined ? { tag } : {}),
  }

  const documentsQuery = useDocuments(params)
  const tagsQuery = useTags()
  const correspondentsQuery = useCorrespondents()
  const documentTypesQuery = useDocumentTypes()
  const customFieldsQuery = useCustomFields()

  const columns = React.useMemo<ColumnDef<DocumentListItem>[]>(
    () => [...buildBaseColumns(), ...buildCustomFieldColumns(customFieldsQuery.data ?? [])],
    [customFieldsQuery.data],
  )

  const rows = documentsQuery.data?.items ?? []

  const table = useReactTable({
    data: rows,
    columns,
    state: { sorting, columnVisibility },
    onSortingChange: setSorting,
    onColumnVisibilityChange: setColumnVisibility,
    getCoreRowModel: getCoreRowModel(),
    manualSorting: true,
    manualPagination: true,
    enableMultiSort: false,
  })

  const total = documentsQuery.data?.total ?? 0
  const pageCount = documentsQuery.data?.page_count ?? 0

  const currentPageIds = rows.map((row) => row.id)
  const allCurrentPageSelected =
    currentPageIds.length > 0 && currentPageIds.every((id) => selection.isSelected(id))

  function toggleSelectCurrentPage() {
    if (allCurrentPageSelected) {
      selection.deselectMany(currentPageIds)
    } else {
      selection.selectMany(currentPageIds)
    }
  }

  function resetFilters() {
    setDocumentType(undefined)
    setCorrespondent(undefined)
    setTag(undefined)
    setSearchInput('')
    setSearch('')
    setPage(1)
  }

  return (
    <div className="mx-auto flex max-w-7xl flex-col gap-4">
      <div>
        <h1 className="text-xl font-semibold">{messages.explorer.title}</h1>
        <p className="text-sm text-muted-foreground">{messages.explorer.subtitle}</p>
      </div>

      {/* Toolbar: search, filters, column visibility, selection summary. */}
      <div className="flex flex-wrap items-center gap-2">
        <input
          type="text"
          value={searchInput}
          onChange={(event) => setSearchInput(event.target.value)}
          placeholder={messages.explorer.searchPlaceholder}
          className="h-9 w-64 rounded-md border border-input bg-background px-3 text-sm focus-ring"
          aria-label={messages.explorer.searchPlaceholder}
        />

        <select
          value={documentType ?? ''}
          onChange={(event) => {
            setDocumentType(event.target.value === '' ? undefined : Number(event.target.value))
            setPage(1)
          }}
          className="h-9 rounded-md border border-input bg-background px-2 text-sm focus-ring"
          aria-label={messages.explorer.filterDocumentType}
        >
          <option value="">{`${messages.explorer.filterDocumentType}: ${messages.explorer.filterAll}`}</option>
          {(documentTypesQuery.data ?? []).map((dt) => (
            <option key={dt.id} value={dt.id}>
              {dt.name}
            </option>
          ))}
        </select>

        <select
          value={correspondent ?? ''}
          onChange={(event) => {
            setCorrespondent(event.target.value === '' ? undefined : Number(event.target.value))
            setPage(1)
          }}
          className="h-9 rounded-md border border-input bg-background px-2 text-sm focus-ring"
          aria-label={messages.explorer.filterCorrespondent}
        >
          <option value="">{`${messages.explorer.filterCorrespondent}: ${messages.explorer.filterAll}`}</option>
          {(correspondentsQuery.data ?? []).map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </select>

        <select
          value={tag ?? ''}
          onChange={(event) => {
            setTag(event.target.value === '' ? undefined : Number(event.target.value))
            setPage(1)
          }}
          className="h-9 rounded-md border border-input bg-background px-2 text-sm focus-ring"
          aria-label={messages.explorer.filterTag}
        >
          <option value="">{`${messages.explorer.filterTag}: ${messages.explorer.filterAll}`}</option>
          {(tagsQuery.data ?? []).map((t) => (
            <option key={t.id} value={t.id}>
              {t.name}
            </option>
          ))}
        </select>

        <Button variant="outline" size="sm" onClick={resetFilters}>
          <X className="h-3.5 w-3.5" aria-hidden="true" />
          {messages.explorer.resetFilters}
        </Button>

        <div className="relative ml-auto">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setColumnsMenuOpen((open) => !open)}
            aria-expanded={columnsMenuOpen}
          >
            <Columns3 className="h-3.5 w-3.5" aria-hidden="true" />
            {messages.explorer.columns}
          </Button>
          {columnsMenuOpen && (
            <div className="absolute right-0 z-10 mt-1 w-56 rounded-md border border-border bg-card p-2 shadow-md">
              {table.getAllLeafColumns().map((column) => (
                <label
                  key={column.id}
                  className="flex items-center gap-2 rounded px-2 py-1 text-sm hover:bg-accent"
                >
                  <input
                    type="checkbox"
                    checked={column.getIsVisible()}
                    onChange={column.getToggleVisibilityHandler()}
                  />
                  <span>
                    {typeof column.columnDef.header === 'string'
                      ? column.columnDef.header
                      : column.id}
                  </span>
                </label>
              ))}
            </div>
          )}
        </div>
      </div>

      {selection.count > 0 && (
        <div className="flex items-center gap-3 rounded-md border border-primary/30 bg-primary/5 px-3 py-2 text-sm">
          <span className="font-medium">
            {messages.explorer.selectedCount.replace('{count}', String(selection.count))}
          </span>
          <Button variant="ghost" size="sm" onClick={selection.clear}>
            {messages.explorer.clearSelection}
          </Button>
        </div>
      )}

      <Card>
        <CardContent className="p-0">
          {documentsQuery.isError ? (
            <div className="flex flex-col items-center gap-3 p-10 text-center">
              <AlertTriangle className="h-8 w-8 text-destructive" aria-hidden="true" />
              <p className="font-medium">{messages.explorer.errorTitle}</p>
              <p className="text-sm text-muted-foreground">
                {documentsQuery.error instanceof Error
                  ? documentsQuery.error.message
                  : messages.errors.generic}
              </p>
              <Button variant="outline" size="sm" onClick={() => documentsQuery.refetch()}>
                {messages.explorer.retry}
              </Button>
            </div>
          ) : documentsQuery.isPending ? (
            <div className="p-10 text-center text-sm text-muted-foreground">
              {messages.explorer.loading}
            </div>
          ) : rows.length === 0 ? (
            <div className="flex flex-col items-center gap-1 p-10 text-center">
              <p className="font-medium">{messages.explorer.empty}</p>
              <p className="text-sm text-muted-foreground">{messages.explorer.emptyBody}</p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="border-b border-border bg-muted/40">
                  <tr>
                    <th className="w-9 px-3 py-2">
                      <input
                        type="checkbox"
                        checked={allCurrentPageSelected}
                        onChange={toggleSelectCurrentPage}
                        aria-label={messages.explorer.selectAll}
                      />
                    </th>
                    {table.getHeaderGroups()[0]?.headers.map((header) => {
                      const sortState = header.column.getIsSorted()
                      return (
                        <th
                          key={header.id}
                          className={cn(
                            'px-3 py-2 text-left font-medium text-muted-foreground',
                            header.column.getCanSort() && 'cursor-pointer select-none',
                          )}
                          onClick={header.column.getToggleSortingHandler()}
                          aria-sort={
                            sortState === 'asc'
                              ? 'ascending'
                              : sortState === 'desc'
                                ? 'descending'
                                : 'none'
                          }
                        >
                          <span className="inline-flex items-center gap-1">
                            {flexRender(header.column.columnDef.header, header.getContext())}
                            {header.column.getCanSort() &&
                              (sortState === 'asc' ? '▲' : sortState === 'desc' ? '▼' : '')}
                          </span>
                        </th>
                      )
                    })}
                  </tr>
                </thead>
                <tbody>
                  {table.getRowModel().rows.map((row) => (
                    <tr
                      key={row.original.id}
                      className={cn(
                        'border-b border-border/60 last:border-0 hover:bg-accent/40',
                        selection.isSelected(row.original.id) && 'bg-primary/5',
                      )}
                    >
                      <td className="px-3 py-2">
                        <input
                          type="checkbox"
                          checked={selection.isSelected(row.original.id)}
                          onChange={() => selection.toggle(row.original.id)}
                          aria-label={messages.explorer.selectRow}
                        />
                      </td>
                      {row.getVisibleCells().map((cell) => (
                        <td key={cell.id} className="px-3 py-2">
                          {flexRender(cell.column.columnDef.cell, cell.getContext())}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Pagination footer - always server-side, never a client-side slice. */}
      <div className="flex flex-wrap items-center justify-between gap-3 text-sm">
        <span className="text-muted-foreground">
          {messages.explorer.showingCount
            .replace('{count}', String(rows.length))
            .replace('{total}', String(total))}
        </span>

        <div className="flex items-center gap-2">
          <label className="flex items-center gap-1.5 text-muted-foreground">
            {messages.explorer.pageSize}
            <select
              value={pageSize}
              onChange={(event) => {
                setPageSize(Number(event.target.value) as DocumentPageSize)
                setPage(1)
              }}
              className="h-8 rounded-md border border-input bg-background px-1.5 text-sm focus-ring"
            >
              {DOCUMENT_PAGE_SIZES.map((size) => (
                <option key={size} value={size}>
                  {size}
                </option>
              ))}
            </select>
          </label>

          <Button
            variant="outline"
            size="icon"
            disabled={page <= 1}
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            aria-label={messages.explorer.previousPage}
          >
            <ChevronLeft className="h-4 w-4" aria-hidden="true" />
          </Button>
          <span className="tabular text-muted-foreground">
            {messages.explorer.page} {page} {messages.explorer.of} {Math.max(pageCount, 1)}
          </span>
          <Button
            variant="outline"
            size="icon"
            disabled={page >= pageCount}
            onClick={() => setPage((p) => Math.min(pageCount, p + 1))}
            aria-label={messages.explorer.nextPage}
          >
            <ChevronRight className="h-4 w-4" aria-hidden="true" />
          </Button>
        </div>
      </div>
    </div>
  )
}
