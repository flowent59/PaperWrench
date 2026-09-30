import { AlertTriangle, BarChart3, Download, Filter, Play } from 'lucide-react'
import * as React from 'react'
import { Link } from 'react-router'

import { analyticsApi } from '@/api/client'
import {
  useCorrespondents,
  useCustomFields,
  useDocumentTypes,
  useFilterCapabilities,
  useFilterValidation,
  useStoragePaths,
  useTags,
} from '@/api/queries'
import type {
  CustomFieldReport,
  CustomFieldReportRequest,
  DashboardRange,
  NumericSemantics,
  ReportGroupBy,
} from '@/api/types'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { errorMessage } from '@/i18n/errors'
import { formatCurrency, formatNumber, intlLocale } from '@/i18n/format'
import { localizeFilterCapabilities } from '@/i18n/filter-capabilities'
import { messages } from '@/i18n/messages'
import { FilterBuilder } from '@/pages/explorer/filter-builder'
import { emptyFilterSet, isEmpty } from '@/pages/explorer/filter-builder/model'
import { exactQueryHref } from '@/pages/quality/navigation'

const SELECT = 'h-9 rounded-md border border-input bg-background px-2 text-sm focus-ring'
const REPORTABLE_TYPES = new Set(['integer', 'float', 'monetary', 'date'])

function valueLabel(value: { value: string; currency: string | null }): string {
  return value.currency === null
    ? new Intl.NumberFormat(intlLocale(), { maximumFractionDigits: 6 }).format(Number(value.value))
    : formatCurrency(value.currency, value.value)
}

function groupLabel(group: CustomFieldReport['groups'][number], groupBy: ReportGroupBy): string {
  if (group.label !== null) {
    if (groupBy === 'month') {
      return new Intl.DateTimeFormat(intlLocale(), { month: 'short', year: 'numeric' })
        .format(new Date(`${group.key}-01T00:00:00Z`))
    }
    return group.label
  }
  return group.key === 'missing'
    ? messages.analytics.unassigned
    : `${messages.analytics.unknown} (#${group.key})`
}

function download(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  URL.revokeObjectURL(url)
}

export function AnalyticsPage() {
  const customFields = useCustomFields()
  const capabilities = useFilterCapabilities()
  const tags = useTags()
  const correspondents = useCorrespondents()
  const documentTypes = useDocumentTypes()
  const storagePaths = useStoragePaths()
  const fields = (customFields.data ?? []).filter((field) => REPORTABLE_TYPES.has(field.data_type))
  const [fieldId, setFieldId] = React.useState<number | null>(null)
  const [range, setRange] = React.useState<DashboardRange>('365d')
  const [groupBy, setGroupBy] = React.useState<ReportGroupBy>('month')
  const [semantics, setSemantics] = React.useState<NumericSemantics>('sum')
  const [filters, setFilters] = React.useState(emptyFilterSet)
  const [filtersOpen, setFiltersOpen] = React.useState(false)
  const [report, setReport] = React.useState<CustomFieldReport | null>(null)
  const [busy, setBusy] = React.useState(false)
  const [exporting, setExporting] = React.useState(false)
  const [error, setError] = React.useState('')
  const selectedField = fields.find((field) => field.id === fieldId)
  const filtersEmpty = isEmpty(filters)
  const validation = useFilterValidation(filters, !filtersEmpty)
  const localizedCapabilities = localizeFilterCapabilities(capabilities.data)
  const filtersValid = filtersEmpty || validation.data?.compilable === true
  const referenceOptions = React.useMemo(() => ({
    tag: tags.data ?? [],
    correspondent: correspondents.data ?? [],
    document_type: documentTypes.data ?? [],
    storage_path: storagePaths.data ?? [],
  }), [tags.data, correspondents.data, documentTypes.data, storagePaths.data])

  const request = React.useCallback((): CustomFieldReportRequest | null => fieldId === null ? null : ({
    field_id: fieldId,
    range,
    group_by: groupBy,
    numeric_semantics: semantics,
    filters: filtersEmpty ? null : filters,
  }), [fieldId, range, groupBy, semantics, filters, filtersEmpty])

  async function runReport() {
    const payload = request()
    if (payload === null) return
    setBusy(true); setError(''); setReport(null)
    try { setReport(await analyticsApi.report(payload)) }
    catch (cause) { setError(errorMessage(cause, messages.analytics.error)) }
    finally { setBusy(false) }
  }

  async function exportReport() {
    const payload = request()
    if (payload === null) return
    setExporting(true); setError('')
    try {
      download(await analyticsApi.exportReport(payload), `paperwrench-${payload.field_id}-${payload.range}.csv`)
    } catch (cause) { setError(errorMessage(cause, messages.analytics.error)) }
    finally { setExporting(false) }
  }

  const maxCount = Math.max(1, ...(report?.groups.map((group) => group.document_count) ?? []))
  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <div>
        <h1 className="text-xl font-semibold">{messages.analytics.title}</h1>
        <p className="text-sm text-muted-foreground">{messages.analytics.subtitle}</p>
      </div>

      <Card>
        <CardContent className="space-y-4 p-4">
          <div className="grid gap-3 md:grid-cols-4">
            <label className="text-sm"><span className="mb-1 block text-muted-foreground">{messages.analytics.field}</span>
              <select className={`${SELECT} w-full`} value={fieldId ?? ''} onChange={(event) => { setFieldId(event.target.value ? Number(event.target.value) : null); setReport(null) }}>
                <option value="">{messages.analytics.chooseField}</option>
                {fields.map((field) => <option key={field.id} value={field.id}>{field.name}</option>)}
              </select>
            </label>
            <label className="text-sm"><span className="mb-1 block text-muted-foreground">{messages.analytics.period}</span>
              <select className={`${SELECT} w-full`} value={range} onChange={(event) => { setRange(event.target.value as DashboardRange); setReport(null) }}>
                <option value="30d">{messages.analytics.range30d}</option><option value="90d">{messages.analytics.range90d}</option><option value="365d">{messages.analytics.range365d}</option>
              </select>
            </label>
            <label className="text-sm"><span className="mb-1 block text-muted-foreground">{messages.analytics.groupBy}</span>
              <select className={`${SELECT} w-full`} value={groupBy} onChange={(event) => { setGroupBy(event.target.value as ReportGroupBy); setReport(null) }}>
                <option value="month">{messages.analytics.month}</option><option value="year">{messages.analytics.year}</option><option value="document_type">{messages.analytics.documentType}</option><option value="correspondent">{messages.analytics.correspondent}</option>
              </select>
            </label>
            {selectedField !== undefined && selectedField.data_type !== 'date' && <label className="text-sm"><span className="mb-1 block text-muted-foreground">{messages.analytics.semantics}</span>
              <select className={`${SELECT} w-full`} value={semantics} onChange={(event) => { setSemantics(event.target.value as NumericSemantics); setReport(null) }}>
                <option value="sum">{messages.analytics.sum}</option><option value="snapshot">{messages.analytics.snapshot}</option>
              </select>
            </label>}
          </div>
          {selectedField !== undefined && selectedField.data_type !== 'date' && <p className="text-xs text-muted-foreground">{semantics === 'sum' ? messages.analytics.sumHelp : messages.analytics.snapshotHelp}</p>}
          {!customFields.isPending && fields.length === 0 && <p className="text-sm text-muted-foreground">{messages.analytics.unsupported}</p>}
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" size="sm" onClick={() => setFiltersOpen((open) => !open)}><Filter className="h-3.5 w-3.5" aria-hidden="true" />{filtersOpen ? messages.analytics.hideFilters : messages.analytics.showFilters}</Button>
            <Button size="sm" disabled={fieldId === null || !filtersValid || busy} onClick={runReport}><Play className="h-3.5 w-3.5" aria-hidden="true" />{busy ? messages.analytics.running : messages.analytics.run}</Button>
            <Button variant="outline" size="sm" disabled={report === null || exporting} onClick={exportReport}><Download className="h-3.5 w-3.5" aria-hidden="true" />{messages.analytics.export}</Button>
          </div>
          {filtersOpen && <div className="border-t pt-4">
            <h2 className="mb-3 text-sm font-medium">{messages.analytics.filters}</h2>
            <FilterBuilder filters={filters} onChange={(value) => { setFilters(value); setReport(null) }} fields={localizedCapabilities?.fields ?? []} grouping={localizedCapabilities?.grouping} issues={validation.data?.issues ?? []} referenceOptions={referenceOptions} />
          </div>}
        </CardContent>
      </Card>

      {error && <Card className="border-destructive/40"><CardContent className="flex gap-2 p-4 text-sm text-destructive"><AlertTriangle className="h-4 w-4 shrink-0" aria-hidden="true" />{error}</CardContent></Card>}
      {report !== null && <>
        {!report.additive && <Card className="border-warning/40 bg-warning/5"><CardContent className="flex gap-2 p-4 text-sm"><AlertTriangle className="h-4 w-4 shrink-0 text-warning" aria-hidden="true" />{messages.analytics.nonAdditive}</CardContent></Card>}
        <div className="grid gap-4 sm:grid-cols-2"><Card><CardHeader><CardTitle>{messages.analytics.matched}</CardTitle></CardHeader><CardContent className="text-3xl font-semibold tabular">{formatNumber(report.matched_documents)}</CardContent></Card><Card><CardHeader><CardTitle>{messages.analytics.valued}</CardTitle></CardHeader><CardContent className="text-3xl font-semibold tabular">{formatNumber(report.valued_documents)}</CardContent></Card></div>
        <Card><CardHeader><div className="flex items-center gap-2"><BarChart3 className="h-4 w-4 text-primary" aria-hidden="true" /><CardTitle>{messages.analytics.results}</CardTitle></div><CardDescription>{report.field_name}</CardDescription></CardHeader><CardContent className="space-y-4">
          {report.groups.length === 0 ? <p className="text-sm text-muted-foreground">{messages.analytics.empty}</p> : report.groups.map((group) => <Link key={group.key} to={exactQueryHref(group.query)} className="focus-ring block rounded-sm"><div className="mb-1 flex flex-wrap justify-between gap-3 text-sm"><span>{groupLabel(group, report.group_by)}</span><span className="tabular">{group.values.length > 0 ? group.values.map(valueLabel).join(' · ') : `${formatNumber(group.document_count)} ${messages.analytics.documents}`}</span></div><div className="h-2 rounded bg-muted"><div className="h-full rounded bg-primary/70" style={{ width: `${group.document_count / maxCount * 100}%` }} /></div></Link>)}
        </CardContent></Card>
        <Card><CardHeader><CardTitle>{messages.analytics.missingTitle}</CardTitle></CardHeader><CardContent className="grid gap-3 sm:grid-cols-3">{report.missing.map((bucket) => { const label = messages.analytics[bucket.kind]; const content = <div className="rounded-md border p-3"><div className="text-sm text-muted-foreground">{label}</div><div className="text-xl font-semibold tabular">{formatNumber(bucket.count)}</div></div>; return bucket.query === null ? <div key={bucket.kind}>{content}</div> : <Link key={bucket.kind} to={exactQueryHref(bucket.query)} className="focus-ring rounded-md">{content}</Link> })}</CardContent></Card>
        <p className="text-xs text-muted-foreground">{messages.analytics.readOnly}</p>
      </>}
    </div>
  )
}
