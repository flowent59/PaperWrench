/**
 * Backend response types.
 *
 * These are hand-written for M0 only. From M1 they are replaced by types
 * generated from the OpenAPI schema (`npm run generate:api` ->
 * `src/api/schema.d.ts`), so that a backend contract change becomes a
 * frontend compile error instead of a runtime surprise.
 */

export interface HealthResponse {
  status: 'ok' | 'degraded'
  version: string
  database: 'ok' | 'error'
}

export interface InfoResponse {
  version: string
  paperless_configured: boolean
  paperless_api_version: number
  supported_paperless_api_version: number
  default_page_size: number
  max_concurrency: number
}
