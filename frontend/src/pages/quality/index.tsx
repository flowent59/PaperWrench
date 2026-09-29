/** Read-only quality summary and bounded violation pages. */
import { useQuery } from '@tanstack/react-query'
import * as React from 'react'
import { Link } from 'react-router'

import { qualityApi, schemasApi } from '@/api/client'
import type { QualityFinding, SchemaRuleResult } from '@/api/types'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { messages } from '@/i18n/messages'
import { formatNumber } from '@/i18n/format'
import { errorMessage } from '@/i18n/errors'

import { exactIdsHref, exactQueryHref } from './navigation'
import { expected, observed } from './format'

const m = messages.quality

function fieldName(rule: SchemaRuleResult): string {
  return rule.field.source === 'core' ? rule.field.name
    : rule.field.display_name || m.customField(rule.field.field_id)
}

function findingKey(item: QualityFinding): string {
  return `${item.document_id}-${item.rule.rule_index}`
}

export function QualityPageView() {
  const schemas = useQuery({ queryKey: ['schemas'], queryFn: schemasApi.list })
  const [schemaId, setSchemaId] = React.useState<number | null>(null)
  const [page, setPage] = React.useState(1)
  const selected = schemaId ?? schemas.data?.[0]?.id ?? null
  const result = useQuery({
    queryKey: ['quality', selected, page],
    queryFn: () => qualityApi.page(selected as number, page),
    enabled: selected !== null,
  })
  const data = result.data

  return <div className="space-y-5 p-6">
    <div>
      <h1 className="text-2xl font-semibold">{m.title}</h1>
      <p className="text-sm text-muted-foreground">{m.subtitle}</p>
    </div>
    {schemas.isError && <p role="alert">{m.schemaError}</p>}
    {schemas.data?.length === 0 && <p>{m.noSchemas} <Link className="underline" to="/schemas">{m.createSchema}</Link>.</p>}
    {schemas.data && schemas.data.length > 0 && <label className="flex items-center gap-2 text-sm">
      {m.schema}
      <select className="rounded-md border border-input bg-background p-2" value={selected ?? ''}
        onChange={event => { setSchemaId(Number(event.target.value)); setPage(1) }}>
        {schemas.data.map(schema => <option key={schema.id} value={schema.id}>{schema.name}</option>)}
      </select>
    </label>}
    {result.isError && <p role="alert">{errorMessage(result.error, m.evaluationError)}</p>}
    {result.isPending && selected !== null && <p>{m.evaluating}</p>}
    {data && <>
      <div className="grid gap-3 sm:grid-cols-3">
        <Card><CardContent className="p-4"><strong>{formatNumber(data.evaluated_count)}</strong><p className="text-sm">{m.evaluatedCount}</p></CardContent></Card>
        <Card><CardContent className="p-4"><strong>{formatNumber(data.violation_count)}</strong><p className="text-sm">{m.violationCount}</p></CardContent></Card>
        <Card><CardContent className="p-4"><strong>{formatNumber(data.dataset_total)}</strong><p className="text-sm">{m.datasetTotal}</p></CardContent></Card>
      </div>
      <Card><CardContent className="space-y-3 p-4">
        <h2 className="font-semibold">{m.rules}</h2>
        {data.rules.map(rule => <div key={rule.rule_index} className="flex flex-wrap items-center gap-3 text-sm">
          <span>{m.ruleCount(rule.rule_index + 1, rule.violation_count)}</span>
          {rule.exact_query && <Link className="underline" to={exactQueryHref(rule.exact_query)}>{m.exactCondition}</Link>}
          {!rule.exact_query && rule.page_document_ids.length > 0 &&
            <Link className="underline" to={exactIdsHref(rule.page_document_ids)}>{m.exactIds}</Link>}
          {!rule.exact_query && rule.page_document_ids.length === 0 &&
            <span className="text-muted-foreground">{m.noDrilldown}</span>}
        </div>)}
      </CardContent></Card>
      <Card><CardContent className="p-0">
        <div className="overflow-x-auto"><table className="w-full text-sm">
          <thead><tr className="border-b text-left"><th className="p-3">{m.document}</th><th className="p-3">{m.field}</th><th className="p-3">{m.observed}</th><th className="p-3">{m.expected}</th><th className="p-3">{m.explorer}</th></tr></thead>
          <tbody>{data.items.map(item => <tr className="border-b last:border-0" key={findingKey(item)}>
            <td className="p-3">{item.title} (#{item.document_id})</td>
            <td className="p-3">{fieldName(item.rule)}</td>
            <td className="p-3 font-mono">{observed(item.rule)}</td>
            <td className="p-3 font-mono">{expected(item.rule)}</td>
            <td className="p-3"><Link className="underline" to={exactIdsHref([item.document_id])}>{m.openDocument}</Link></td>
          </tr>)}</tbody>
        </table></div>
        {data.items.length === 0 && <p className="p-4 text-sm">{m.noViolations}</p>}
      </CardContent></Card>
      <div className="flex items-center gap-3 text-sm">
        <Button variant="outline" disabled={page <= 1} onClick={() => setPage(value => value - 1)}>{m.previous}</Button>
        <span>{m.page(page, data.page_count || 1)}</span>
        <Button variant="outline" disabled={page >= data.page_count} onClick={() => setPage(value => value + 1)}>{m.next}</Button>
      </div>
    </>}
  </div>
}
