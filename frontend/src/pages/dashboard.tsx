import { AlertTriangle, BarChart3, Database, FileText } from 'lucide-react'
import * as React from 'react'
import { Link } from 'react-router'

import { useDashboard } from '@/api/queries'
import type { DashboardBreakdown, DashboardBreakdownItem, DashboardPeriod, DashboardRange } from '@/api/types'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { errorMessage } from '@/i18n/errors'
import { formatDateTime, formatDateValue, formatNumber } from '@/i18n/format'
import { messages } from '@/i18n/messages'
import { exactQueryHref } from '@/pages/quality/navigation'

const RANGE_LABELS: Record<DashboardRange, () => string> = {
  '30d': () => messages.dashboard.range30d,
  '90d': () => messages.dashboard.range90d,
  '365d': () => messages.dashboard.range365d,
}

function PeriodChart({ periods }: { periods: DashboardPeriod[] }) {
  const maximum = Math.max(1, ...periods.map((period) => period.count))
  return (
    <div className="flex h-52 items-end gap-1" role="img" aria-label={messages.dashboard.trendTitle}>
      {periods.map((period) => {
        const label = `${formatDateValue(period.start)} – ${formatNumber(period.count)}`
        return (
          <Link
            key={period.start}
            to={exactQueryHref(period.query)}
            className="focus-ring group flex min-w-0 flex-1 flex-col items-center justify-end rounded-sm"
            title={label}
            aria-label={`${label}. ${messages.dashboard.viewInExplorer}`}
          >
            <span className="mb-1 text-[10px] tabular text-muted-foreground opacity-0 group-hover:opacity-100 group-focus:opacity-100">
              {formatNumber(period.count)}
            </span>
            <span
              className="w-full min-w-1 rounded-t bg-primary/75 transition-colors group-hover:bg-primary group-focus:bg-primary"
              style={{ height: `${Math.max(period.count === 0 ? 2 : 8, (period.count / maximum) * 160)}px` }}
            />
          </Link>
        )
      })}
    </div>
  )
}

function breakdownLabel(item: DashboardBreakdownItem): string {
  return item.label ?? (item.id === null
    ? messages.dashboard.unassigned
    : `${messages.dashboard.unknown} (#${item.id})`)
}

function BreakdownCard({ breakdown }: { breakdown: DashboardBreakdown }) {
  const title = breakdown.dimension === 'correspondent'
    ? messages.dashboard.correspondent
    : breakdown.dimension === 'document_type'
      ? messages.dashboard.documentType
      : messages.dashboard.tags
  const shown = breakdown.items.slice(0, 8)
  const maximum = Math.max(1, ...shown.map((item) => item.count))
  return (
    <Card>
      <CardHeader><CardTitle>{title}</CardTitle></CardHeader>
      <CardContent className="space-y-3">
        {shown.map((item) => (
          <Link
            key={`${breakdown.dimension}-${item.id ?? 'none'}`}
            to={exactQueryHref(item.query)}
            className="focus-ring block rounded-sm"
            aria-label={`${breakdownLabel(item)}: ${formatNumber(item.count)}. ${messages.dashboard.viewInExplorer}`}
          >
            <div className="mb-1 flex justify-between gap-3 text-sm">
              <span className="truncate">{breakdownLabel(item)}</span>
              <span className="tabular text-muted-foreground">{formatNumber(item.count)}</span>
            </div>
            <div className="h-1.5 rounded bg-muted">
              <div className="h-full rounded bg-primary/70" style={{ width: `${(item.count / maximum) * 100}%` }} />
            </div>
          </Link>
        ))}
      </CardContent>
    </Card>
  )
}

export function DashboardPage() {
  const [range, setRange] = React.useState<DashboardRange>('30d')
  const dashboard = useDashboard(range)
  const snapshot = dashboard.data

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold">{messages.dashboard.title}</h1>
          <p className="text-sm text-muted-foreground">{messages.dashboard.subtitle}</p>
        </div>
        <div>
          <div className="mb-1 text-xs font-medium text-muted-foreground">{messages.dashboard.rangeLabel}</div>
          <div className="flex gap-1" role="group" aria-label={messages.dashboard.rangeLabel}>
            {(Object.keys(RANGE_LABELS) as DashboardRange[]).map((value) => (
              <Button key={value} variant={range === value ? 'default' : 'outline'} size="sm" onClick={() => setRange(value)}>
                {RANGE_LABELS[value]()}
              </Button>
            ))}
          </div>
        </div>
      </div>

      {dashboard.isPending && <p className="text-sm text-muted-foreground">{messages.dashboard.loading}</p>}
      {dashboard.isError && (
        <Card className="border-destructive/40">
          <CardHeader className="flex-row items-center gap-2 space-y-0">
            <AlertTriangle className="h-4 w-4 text-destructive" aria-hidden="true" />
            <CardTitle>{messages.dashboard.errorTitle}</CardTitle>
          </CardHeader>
          <CardContent className="text-sm text-muted-foreground">
            {errorMessage(dashboard.error, messages.errors.generic)}
          </CardContent>
        </Card>
      )}

      {snapshot !== undefined && (
        <>
          <div className="grid gap-4 sm:grid-cols-2">
            <Card>
              <CardHeader className="flex-row items-center gap-2 space-y-0">
                <Database className="h-4 w-4 text-primary" aria-hidden="true" />
                <CardTitle>{messages.dashboard.totalVisible}</CardTitle>
              </CardHeader>
              <CardContent className="text-3xl font-semibold tabular">{formatNumber(snapshot.total_visible)}</CardContent>
            </Card>
            <Card>
              <CardHeader className="flex-row items-center gap-2 space-y-0">
                <FileText className="h-4 w-4 text-primary" aria-hidden="true" />
                <CardTitle>{messages.dashboard.documentsInRange}</CardTitle>
              </CardHeader>
              <CardContent className="text-3xl font-semibold tabular">{formatNumber(snapshot.documents_in_range)}</CardContent>
            </Card>
          </div>

          <Card>
            <CardHeader>
              <div className="flex items-center gap-2"><BarChart3 className="h-4 w-4 text-primary" aria-hidden="true" /><CardTitle>{messages.dashboard.trendTitle}</CardTitle></div>
              <CardDescription>{messages.dashboard.trendDescription}</CardDescription>
            </CardHeader>
            <CardContent>
              {snapshot.documents_in_range === 0 ? <p className="text-sm text-muted-foreground">{messages.dashboard.empty}</p> : <PeriodChart periods={snapshot.trend} />}
            </CardContent>
          </Card>

          <div className="grid gap-4 lg:grid-cols-3">
            {snapshot.breakdowns.map((breakdown) => <BreakdownCard key={breakdown.dimension} breakdown={breakdown} />)}
          </div>

          <Card>
            <CardHeader>
              <CardTitle>{messages.dashboard.customFields}</CardTitle>
              <CardDescription>{messages.dashboard.customFieldsDescription}</CardDescription>
            </CardHeader>
            <CardContent className="grid gap-3 md:grid-cols-2">
              {snapshot.custom_fields.map((field) => (
                <div key={field.field_id} className="rounded-md border p-3">
                  <div className="mb-2 truncate text-sm font-medium">{field.label}</div>
                  <div className="flex gap-4 text-xs">
                    <Link className="focus-ring rounded text-primary hover:underline" to={exactQueryHref(field.present_query)}>{messages.dashboard.present}: {formatNumber(field.present)}</Link>
                    <Link className="focus-ring rounded text-muted-foreground hover:underline" to={exactQueryHref(field.missing_query)}>{messages.dashboard.missing}: {formatNumber(field.missing)}</Link>
                  </div>
                </div>
              ))}
            </CardContent>
          </Card>

          <p className="flex items-center justify-between text-xs text-muted-foreground">
            <span>{messages.dashboard.readOnly}</span>
            <span>{messages.dashboard.updated}: {formatDateTime(snapshot.generated_at)}</span>
          </p>
        </>
      )}
    </div>
  )
}
