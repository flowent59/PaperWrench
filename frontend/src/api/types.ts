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

/**
 * Result of a live probe against Paperless-ngx (`GET /system/paperless`).
 *
 * Contains no credential: `url` is operator configuration, the token is never
 * serialised in any form, not even redacted.
 *
 * `api_version` is the *highest* API version the server supports, not the one
 * that was negotiated. Paperless sets `X-Api-Version` unconditionally from
 * `ALLOWED_VERSIONS[-1]`, so it must never be compared for equality with the
 * version PaperWrench requested (see ADR-0008). `compatible` already carries
 * the verdict; the UI displays `api_version` as information only.
 */
export interface PaperlessStatusResponse {
  configured: boolean
  connected: boolean
  compatible: boolean
  url: string | null
  api_version: string | null
  paperless_version: string | null
  requested_api_version: number | null
  document_count: number | null
  error_code: string | null
  error_message: string | null
}
