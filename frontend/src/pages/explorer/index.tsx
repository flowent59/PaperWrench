/**
 * The Explorer - M3's headline deliverable, rebuilt on the Filter Engine in M4.
 *
 * Read-only browsing of the Paperless-ngx library: server-side pagination,
 * server-side sorting (allowlisted on the backend), full-text search with an
 * explicit mode, column visibility, multi-selection persisted across page
 * navigation by document id, and dynamic custom-field columns pulled live
 * from the Metadata Registry.
 *
 * M4 replaced M3's three ad-hoc dropdowns (document type / correspondent /
 * tag) with the Filter Builder. There is now exactly one filtering path -
 * FilterSet -> backend validation -> compiler -> Paperless - rather than a
 * simple one and a real one, which would have drifted apart the first time
 * they disagreed about what "no value" means.
 *
 * A dataset here is `search + filters + ordering`; the page and page size
 * only choose which window of it to render. Nothing on this screen ever
 * fetches more than one page, whatever the filter matches.
 *
 * This component only reads. The single POST it issues is
 * `/documents/query`, which is a read expressed as a POST because a filter
 * tree does not belong in a query string. Titles open the M5 Inspector,
 * whose explicit saves use the separate single-document mutation API.
 */

import {
  flexRender,
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
  type SortingState,
} from '@tanstack/react-table'
import {
  AlertTriangle,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Columns3,
  SlidersHorizontal,
  X,
} from 'lucide-react'
import * as React from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useLocation } from 'react-router'

import { collectionsApi, documentsApi } from '@/api/client'

import {
  useCorrespondents,
  useCustomFields,
  useDocumentTypes,
  useDocuments,
  useFilterCapabilities,
  useFilterCount,
  useFilterValidation,
  useStoragePaths,
  useTags,
} from '@/api/queries'
import type {
  DatasetPageRequest,
  DocumentListItem,
  DocumentPageSize,
  FilterSet,
  SearchMode,
} from '@/api/types'
import { DOCUMENT_PAGE_SIZES } from '@/api/types'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { messages } from '@/i18n/messages'
import { cn } from '@/lib/utils'

import { buildBaseColumns, buildCustomFieldColumns } from './columns'
import { usePersistedColumnVisibility } from './column-visibility'
import { FilterBuilder } from './filter-builder'
import { emptyFilterSet, isEmpty } from './filter-builder/model'
import { useDocumentSelection } from './selection'
import { qualityLocation } from '@/pages/quality/navigation'

/** Maps a TanStack Table sort state to the single `ordering` key PaperWrench accepts. */
function sortingToOrdering(sorting: SortingState): string | undefined {
  const first = sorting[0]
  if (first === undefined) return undefined
  return first.desc ? `-${first.id}` : first.id
}

export function ExplorerPage() {
  const queryClient = useQueryClient()
  const collections = useQuery({ queryKey: ['collections'], queryFn: collectionsApi.list })
  const [collectionName, setCollectionName] = React.useState('')
  const [collectionId, setCollectionId] = React.useState('')
  const [collectionError, setCollectionError] = React.useState('')
  const [collectionBusy, setCollectionBusy] = React.useState(false)
  const location = useLocation()
  const qualityTarget = React.useMemo(() => qualityLocation(location.search), [location.search])
  const explicitIds = qualityTarget.ids
  const initialQuery = qualityTarget.query
  const [page, setPage] = React.useState(1)
  const [pageSize, setPageSize] = React.useState<DocumentPageSize>(100)
  const [searchInput, setSearchInput] = React.useState(initialQuery?.search?.text ?? '')
  const [search, setSearch] = React.useState(initialQuery?.search?.text ?? '')
  const [searchMode, setSearchMode] = React.useState<SearchMode>(initialQuery?.search?.mode ?? 'title')
  const [sorting, setSorting] = React.useState<SortingState>(() => {
    const value = initialQuery?.ordering
    return value ? [{ id: value.startsWith('-') ? value.slice(1) : value,
      desc: value.startsWith('-') }] : []
  })
  const [filters, setFilters] = React.useState<FilterSet>(initialQuery?.filters ?? emptyFilterSet)
  const [filtersOpen, setFiltersOpen] = React.useState(false)
  const [showCompiled, setShowCompiled] = React.useState(false)
  const [columnVisibility, setColumnVisibility] = usePersistedColumnVisibility()
  const [columnsMenuOpen, setColumnsMenuOpen] = React.useState(false)
  const selection = useDocumentSelection()
  async function saveSelection() {
    const ids = [...selection.selected].sort((a, b) => a - b)
    setCollectionBusy(true)
    setCollectionError('')
    try {
      if (collectionId) {
        await collectionsApi.add(Number(collectionId), ids)
      } else {
        await collectionsApi.create({ name: collectionName, description: null, document_ids: ids })
        setCollectionName('')
      }
      await queryClient.invalidateQueries({ queryKey: ['collections'] })
      selection.clear()
    } catch (error) {
      setCollectionError(error instanceof Error ? error.message : messages.collections.error)
    } finally {
      setCollectionBusy(false)
    }
  }

  React.useEffect(() => {
    const target = qualityLocation(location.search).query
    setPage(1)
    setSearchInput(target?.search?.text ?? '')
    setSearch(target?.search?.text ?? '')
    setSearchMode(target?.search?.mode ?? 'title')
    setFilters(target?.filters ?? emptyFilterSet())
    const value = target?.ordering
    setSorting(value ? [{ id: value.startsWith('-') ? value.slice(1) : value,
      desc: value.startsWith('-') }] : [])
  }, [location.search]) // Reopen a different stable Quality URL in the same Explorer route.

  // Debounce the search box so every keystroke does not fire a request.
  React.useEffect(() => {
    const handle = window.setTimeout(() => {
      setSearch(searchInput)
      setPage(1)
    }, 350)
    return () => window.clearTimeout(handle)
  }, [searchInput])

  const capabilitiesQuery = useFilterCapabilities()

  // The backend decides whether the current filter is valid and whether
  // Paperless can express it. Cheap enough to ask on every edit: it touches
  // the metadata cache and pure logic, never Paperless.
  const filtersEmpty = isEmpty(filters)
  const validation = useFilterValidation(filters, !filtersEmpty)
  const filterIssues = validation.data?.issues ?? []

  // Three states, not two, and the difference matters for what is on screen:
  //   - runnable: no filter, or one the backend confirmed compilable
  //   - known bad: the backend has answered, and Paperless cannot express it
  //   - in between: still being validated
  // While in between, the request is withheld (no 422 to collect) but no
  // refusal is shown either - the previous page stays put rather than
  // flashing an error at someone who is still building the filter.
  const filtersRunnable = filtersEmpty || validation.data?.compilable === true
  const filtersKnownBad =
    !filtersEmpty && validation.data !== undefined && !validation.data.compilable

  const searchSpec = search.trim() === '' ? null : { mode: searchMode, text: search }

  // Only ask for documents once the filter is known to be runnable. Sending
  // an uncompilable filter would just collect a 422; more importantly, the
  // grid must never show rows produced by a *different* filter than the one
  // on screen.
  const ordering = sortingToOrdering(sorting)
  const request: DatasetPageRequest = {
    page,
    page_size: pageSize,
    ...(searchSpec !== null ? { search: searchSpec } : {}),
    ...(filtersEmpty ? {} : { filters }),
    ...(ordering !== undefined ? { ordering } : {}),
  }

  const datasetDocuments = useDocuments(request, filtersRunnable && explicitIds === null && !qualityTarget.invalid)
  const idsDocuments = useQuery({
    queryKey: ['documents', 'exact-ids', explicitIds],
    queryFn: () => documentsApi.byIds(explicitIds ?? []),
    enabled: explicitIds !== null,
  })
  const documentsQuery = explicitIds === null ? datasetDocuments : idsDocuments

  // The count comes from the Filter Engine, which asks Paperless for its own
  // `count` without fetching anything. It is shown next to the page total so
  // a disagreement between "what the grid is paging through" and "what a
  // later operation would act on" would be visible rather than silent.
  const countQuery = useFilterCount(filters, searchSpec, explicitIds === null && !filtersEmpty && filtersRunnable)

  const tagsQuery = useTags()
  const correspondentsQuery = useCorrespondents()
  const documentTypesQuery = useDocumentTypes()
  const storagePathsQuery = useStoragePaths()
  const customFieldsQuery = useCustomFields()

  // Reference pickers for the builder, keyed by the `reference_kind` the
  // backend attaches to each field. Nothing is hardcoded per field name.
  const referenceOptions = React.useMemo(
    () => ({
      tag: tagsQuery.data ?? [],
      correspondent: correspondentsQuery.data ?? [],
      document_type: documentTypesQuery.data ?? [],
      storage_path: storagePathsQuery.data ?? [],
    }),
    [
      tagsQuery.data,
      correspondentsQuery.data,
      documentTypesQuery.data,
      storagePathsQuery.data,
    ],
  )

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
    setFilters(emptyFilterSet())
    setSearchInput('')
    setSearch('')
    setPage(1)
  }

  function onFiltersChange(next: FilterSet) {
    setFilters(next)
    // Any filter change redefines the dataset, so the current page number is
    // meaningless against it - page 7 of the old result set is not page 7 of
    // the new one.
    setPage(1)
  }

  return (
    <div className="mx-auto flex max-w-7xl flex-col gap-4">
      <div>
        <h1 className="text-xl font-semibold">{messages.explorer.title}</h1>
        <p className="text-sm text-muted-foreground">{messages.explorer.subtitle}</p>
      </div>

      {qualityTarget.invalid && <div role="alert" className="rounded-md border border-destructive p-3 text-sm">
        {messages.quality.invalidDrilldown}{' '}
        <Link className="underline" to="/documents">{messages.quality.clearDrilldown}</Link>
      </div>}
      {explicitIds !== null && <div className="rounded-md border border-border p-3 text-sm">
        {messages.quality.exactIdsSelection(explicitIds.length)}
        {idsDocuments.data && idsDocuments.data.unavailable_count > 0 &&
          <span> {messages.quality.unavailable(idsDocuments.data.unavailable_count)}</span>}
        {' '}<Link className="underline" to="/documents">{messages.quality.clearSelection}</Link>
      </div>}
      {initialQuery !== null && <div className="rounded-md border border-border p-3 text-sm">
        {messages.quality.exactQuerySelection} <Link className="underline" to="/documents">{messages.quality.clearDrilldown}</Link>
      </div>}

      {/* Toolbar: search, filters, column visibility, selection summary. */}
      <div className="flex flex-wrap items-center gap-2">
        <input
          type="text"
          disabled={explicitIds !== null}
          value={searchInput}
          onChange={(event) => setSearchInput(event.target.value)}
          placeholder={messages.explorer.searchPlaceholder}
          className="h-9 w-64 rounded-md border border-input bg-background px-3 text-sm focus-ring"
          aria-label={messages.explorer.searchPlaceholder}
        />

        <select
          disabled={explicitIds !== null}
          value={searchMode}
          onChange={(event) => {
            setSearchMode(event.target.value as SearchMode)
            setPage(1)
          }}
          className="h-9 rounded-md border border-input bg-background px-2 text-sm focus-ring"
          aria-label={messages.filters.searchMode}
        >
          {/* The three modes are three different Paperless indexes, not one
              "search" with options. M3's single search box always meant
              title and said so nowhere. */}
          {(capabilitiesQuery.data?.search_modes ?? []).map((mode) => (
            <option key={mode.mode} value={mode.mode} title={mode.description}>
              {mode.label}
            </option>
          ))}
        </select>

        <Button
          disabled={explicitIds !== null}
          variant={filtersOpen ? 'default' : 'outline'}
          size="sm"
          onClick={() => setFiltersOpen((open) => !open)}
          aria-expanded={filtersOpen}
        >
          <SlidersHorizontal className="h-3.5 w-3.5" aria-hidden="true" />
          {filtersOpen ? messages.filters.hide : messages.filters.show}
          {!filtersEmpty && (
            <span className="ml-1 rounded-full bg-primary/20 px-1.5 text-xs tabular">
              {filters.root.children.length}
            </span>
          )}
        </Button>

        <Button variant="outline" size="sm" disabled={explicitIds !== null} onClick={resetFilters}>
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

      {/* The Filter Builder. Its field list, operator lists and grouping
          rules all come from the backend's capabilities endpoint, so it can
          only build shapes the compiler can translate - and the verdict
          underneath is the backend's, not a local guess. */}
      {filtersOpen && explicitIds === null && !qualityTarget.invalid && (
        <Card>
          <CardContent className="flex flex-col gap-4 p-4">
            {capabilitiesQuery.isPending ? (
              <p className="text-sm text-muted-foreground">{messages.explorer.loading}</p>
            ) : capabilitiesQuery.isError ? (
              <p className="text-sm text-destructive">{messages.errors.generic}</p>
            ) : (
              <>
                <FilterBuilder
                  filters={filters}
                  onChange={onFiltersChange}
                  fields={capabilitiesQuery.data?.fields ?? []}
                  grouping={capabilitiesQuery.data?.grouping}
                  issues={filterIssues}
                  referenceOptions={referenceOptions}
                />

                <div className="flex flex-wrap items-center gap-3 border-t border-border pt-3 text-sm">
                  {filtersEmpty ? (
                    <span className="text-muted-foreground">
                      {messages.filters.noFilters}
                    </span>
                  ) : validation.data === undefined ? (
                    <span className="text-muted-foreground">{messages.filters.counting}</span>
                  ) : validation.data.valid && validation.data.compilable ? (
                    <span className="flex items-center gap-1.5 text-emerald-600 dark:text-emerald-500">
                      <CheckCircle2 className="h-4 w-4" aria-hidden="true" />
                      {messages.filters.valid}
                    </span>
                  ) : validation.data.valid ? (
                    // Valid but not compilable: not a user error. Paperless
                    // simply cannot express this question, and PaperWrench
                    // will not run an approximation of it.
                    <span className="flex items-center gap-1.5 text-amber-600 dark:text-amber-500">
                      <AlertTriangle className="h-4 w-4" aria-hidden="true" />
                      {messages.filters.notCompilable}
                    </span>
                  ) : (
                    <span className="flex items-center gap-1.5 text-destructive">
                      <AlertTriangle className="h-4 w-4" aria-hidden="true" />
                      {messages.filters.invalid}
                    </span>
                  )}

                  {!filtersEmpty && filtersRunnable && (
                    <span className="text-muted-foreground">
                      {countQuery.data === undefined
                        ? messages.filters.counting
                        : messages.filters.matching.replace(
                            '{count}',
                            String(countQuery.data.count),
                          )}
                    </span>
                  )}

                  <Button variant="ghost" size="sm" onClick={resetFilters}>
                    <X className="h-3.5 w-3.5" aria-hidden="true" />
                    {messages.filters.clearAll}
                  </Button>

                  {validation.data?.compiled != null && (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="ml-auto"
                      onClick={() => setShowCompiled((open) => !open)}
                    >
                      {showCompiled
                        ? messages.filters.hideCompiled
                        : messages.filters.showCompiled}
                    </Button>
                  )}
                </div>

                {/* What will actually be asked of Paperless. Shown on demand
                    rather than hidden: a filter engine that cannot show its
                    working is one you have to take on trust. */}
                {showCompiled && validation.data?.compiled != null && (
                  <pre className="overflow-x-auto rounded-md bg-muted p-3 text-xs">
                    {Object.entries(validation.data.compiled.params)
                      .map(([key, value]) => `${key}=${value}`)
                      .join('\n') || messages.filters.noFilters}
                  </pre>
                )}
              </>
            )}
          </CardContent>
        </Card>
      )}

      {selection.count > 0 && (
        <div className="flex flex-wrap items-center gap-3 rounded-md border border-primary/30 bg-primary/5 px-3 py-2 text-sm">
          <span className="font-medium">
            {messages.explorer.selectedCount.replace('{count}', String(selection.count))}
          </span>
          <Button variant="ghost" size="sm" onClick={selection.clear}>
            {messages.explorer.clearSelection}
          </Button>
          <Link className="underline" to="/transformations"
            state={{ targets: { source: 'ids', document_ids: [...selection.selected].sort((a, b) => a - b) } }}>
            {messages.preview.selected}
          </Link>
          <select aria-label={messages.collections.destination} value={collectionId}
            onChange={event => setCollectionId(event.target.value)} className="rounded border bg-background px-2 py-1">
            <option value="">{messages.collections.new}</option>
            {collections.data?.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
          </select>
          {!collectionId && <input aria-label={messages.collections.name} value={collectionName}
            onChange={event => setCollectionName(event.target.value)} placeholder={messages.collections.name}
            className="rounded border bg-background px-2 py-1" />}
          <Button size="sm" disabled={collectionBusy || (!collectionId && !collectionName.trim())}
            onClick={saveSelection}>{messages.collections.saveSelection}</Button>
          {collectionError && <span role="alert" className="text-destructive">{collectionError}</span>}
        </div>
      )}

      {explicitIds === null && !qualityTarget.invalid && filtersRunnable && !documentsQuery.isPending && !documentsQuery.isError && total > 0 &&
        <Link className="text-sm underline" to="/transformations"
          state={{ targets: { source: 'dataset', query: { search: searchSpec,
            filters: filtersEmpty ? null : filters, ordering: ordering ?? null } } }}>
          {messages.preview.dataset}
        </Link>}

      <Card>
        <CardContent className="p-0">
          {qualityTarget.invalid ? (
            <div className="p-10 text-center text-sm">{messages.quality.invalidDrilldownGrid}</div>
          ) : filtersKnownBad ? (
            <div className="flex flex-col items-center gap-2 p-10 text-center">
              <AlertTriangle className="h-8 w-8 text-amber-500" aria-hidden="true" />
              <p className="font-medium">{messages.filters.notCompilable}</p>
              <p className="max-w-md text-sm text-muted-foreground">
                {messages.filters.notCompilableBody}
              </p>
            </div>
          ) : documentsQuery.isError ? (
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
              disabled={explicitIds !== null}
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
