import type { DatasetQuery } from '@/api/types'

/** Durable Explorer URLs. These are selections, never approximate filters. */
export function exactQueryHref(query: DatasetQuery): string {
  return `/documents?quality_query=${encodeURIComponent(JSON.stringify(query))}`
}

export function exactIdsHref(ids: number[]): string {
  return `/documents?quality_ids=${[...new Set(ids)].sort((a, b) => a - b).join(',')}`
}

export function qualityLocation(search: string): {
  query: DatasetQuery | null; ids: number[] | null; invalid: boolean
} {
  const params = new URLSearchParams(search)
  const rawIds = params.get('quality_ids')
  const rawQuery = params.get('quality_query')
  if (rawIds !== null && rawQuery !== null) return { query: null, ids: null, invalid: true }
  if (rawIds !== null) {
    const ids = rawIds.split(',').map(Number)
    if (ids.length > 0 && ids.length <= 100 && ids.every(id => Number.isSafeInteger(id) && id > 0)) {
      return { query: null, ids: [...new Set(ids)], invalid: false }
    }
    return { query: null, ids: null, invalid: true }
  }
  if (rawQuery !== null) {
    try {
      const query: unknown = JSON.parse(rawQuery)
      if (query !== null && typeof query === 'object' && !Array.isArray(query)) {
        const candidate = query as DatasetQuery
        if (candidate.filters?.root?.kind === 'group' &&
            Array.isArray(candidate.filters.root.children) &&
            candidate.filters.root.children.length > 0) {
          return { query: candidate, ids: null, invalid: false }
        }
      }
    } catch { /* invalid drill-down is refused below */ }
    return { query: null, ids: null, invalid: true }
  }
  return { query: null, ids: null, invalid: false }
}
