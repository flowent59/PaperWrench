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

/**
 * The Explorer's normalized documents API (M3).
 *
 * Deliberately NOT Paperless's own `DocumentSerializer` or its
 * `{count, next, previous, results}` envelope - see
 * `backend/src/paperwrench/api/v1/documents.py`. Every reference field is
 * already resolved to a display name, or explicitly unresolved (see
 * `MetadataRef`), and custom fields keep the ABSENT/NULL/PRESENT
 * distinction and Decimal-safe monetary amounts intact.
 */

/**
 * A resolved reference to a tag/correspondent/document type.
 *
 * `name === null` means the id could not be resolved against the Metadata
 * Registry (e.g. the object was deleted upstream after this document last
 * referenced it). Render this as `Unknown (#id)` - never drop the
 * reference and never treat it as "no reference set", which is a real,
 * distinct state (the whole field being `null`).
 */
export interface MetadataRef {
  id: number
  name: string | null
}

/** A monetary custom field value. `amount` is a decimal string, never a JS float. */
export interface MonetaryValueDto {
  currency: string
  amount: string
}

export type CustomFieldValueKind = 'absent' | 'null' | 'present'

/**
 * The typed value of one custom field on one document row.
 *
 * Mirrors the backend's `TypedCustomFieldValue` on purpose: `kind`
 * distinguishes ABSENT / NULL / PRESENT (a present value can still be
 * `""`, `0` or `false` - those are real values, never collapsed into
 * "empty"), `raw` is the untouched source value, and `monetary`/
 * `select_*` are derived conveniences layered on top of it.
 */
export interface CustomFieldColumnValue {
  field_id: number
  kind: CustomFieldValueKind
  raw: unknown
  monetary: MonetaryValueDto | null
  select_option_id: string | null
  select_label: string | null
}

/** One row of the Explorer grid. */
export interface DocumentListItem {
  id: number
  title: string
  correspondent: MetadataRef | null
  document_type: MetadataRef | null
  tags: MetadataRef[]
  created: string | null
  modified: string | null
  added: string | null
  archive_serial_number: number | null
  custom_fields: CustomFieldColumnValue[]
  /** Preserved for later milestones (Inspector edit-affordance); no special
   *  UI treatment in M3 itself. */
  user_can_change: boolean | null
}

/**
 * The paginated envelope the Explorer actually consumes.
 *
 * Deliberately not Paperless's `{count, next, previous, results}`: a
 * page-number UI needs a page count, not an "is there a next" flag, and
 * `next`/`previous` would be absolute URLs built from Paperless's own idea
 * of its hostname (unusable behind a reverse proxy).
 */
export interface DocumentPage {
  items: DocumentListItem[]
  page: number
  page_size: number
  total: number
  page_count: number
}

/** The page sizes the Explorer may request. Deliberately finite - no "ALL". */
export const DOCUMENT_PAGE_SIZES = [25, 50, 100, 250] as const
export type DocumentPageSize = (typeof DOCUMENT_PAGE_SIZES)[number]

/** Query parameters accepted by `GET /api/v1/documents`. */
export interface ListDocumentsParams {
  page?: number
  page_size?: DocumentPageSize
  search?: string
  query?: string
  ordering?: string
  document_type?: number
  correspondent?: number
  tag?: number
}

/**
 * A custom field *definition* (`/api/v1/metadata/custom-fields`).
 *
 * Used by the Explorer to build dynamic columns - never hardcoded. The
 * three data types M3 exposes ordering for are `string`, `longtext`,
 * `monetary` and `date` (see the backend's `SORTABLE_CUSTOM_FIELD_DATA_TYPES`).
 */
export interface CustomFieldDefinition {
  id: number
  name: string
  data_type: string
  extra_data: Record<string, unknown>
}

export interface TagDefinition {
  id: number
  name: string
  slug: string
  color: string | null
}

export interface CorrespondentDefinition {
  id: number
  name: string
  slug: string
}

export interface DocumentTypeDefinition {
  id: number
  name: string
  slug: string
}
