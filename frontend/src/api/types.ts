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

/*
 * ---------------------------------------------------------------------------
 * The Filter Engine (M4)
 *
 * These types mirror `backend/src/paperwrench/filters/` exactly. They are
 * hand-written for now (like everything else in this file) but they are NOT
 * where the rules live: which operators a field allows, and which tree shapes
 * compile, come from `GET /api/v1/filters/capabilities` at runtime. The
 * frontend renders the backend's rules; it never reimplements them, because
 * two copies of a capability matrix drift apart at the first change.
 * ---------------------------------------------------------------------------
 */

/** A reference to a fixed field of the Paperless document schema. */
export interface CoreFieldRef {
  source: 'core'
  name: string
}

/**
 * A reference to a user-defined custom field, **by its stable Paperless id**.
 *
 * `display_name` is for rendering a stored filter without a metadata
 * round-trip. It is never the identity: renaming a custom field in Paperless
 * must not change what a saved filter means, so only `field_id` is ever
 * compared or sent.
 */
export interface CustomFieldRef {
  source: 'custom_field'
  field_id: number
  display_name?: string | null
}

export type FieldRef = CoreFieldRef | CustomFieldRef

export type FilterOperator =
  | 'equals'
  | 'not_equals'
  | 'in'
  | 'contains'
  | 'starts_with'
  | 'ends_with'
  | 'greater_than'
  | 'greater_or_equal'
  | 'less_than'
  | 'less_or_equal'
  | 'has_all_of'
  | 'has_any_of'
  | 'has_none_of'
  | 'is_missing'
  | 'is_present'
  | 'is_null'
  | 'has_value'
  | 'is_empty'

export interface FilterCondition {
  kind: 'condition'
  field: FieldRef
  operator: FilterOperator
  value?: unknown
}

export interface FilterGroup {
  kind: 'group'
  operator: 'and' | 'or'
  children: FilterNode[]
}

/** Representable, but not compiled in this version - see the capabilities. */
export interface FilterNot {
  kind: 'not'
  child: FilterNode
}

export type FilterNode = FilterCondition | FilterGroup | FilterNot

export interface FilterSet {
  root: FilterGroup
}

/**
 * Full-text search, deliberately NOT a filter condition (ADR-0010).
 *
 * `title` searches titles only, `content` searches the extracted text, and
 * `advanced` is raw Paperless query syntax passed through untouched. M3's
 * single `search` parameter always meant `title` and said so nowhere.
 */
export type SearchMode = 'title' | 'content' | 'advanced'

export interface SearchSpec {
  mode: SearchMode
  text: string
}

/**
 * The body of `POST /api/v1/documents/query`.
 *
 * `search + filters + ordering` is the dataset's identity; `page`/`page_size`
 * only describe which window of it to render.
 */
export interface DatasetPageRequest {
  search?: SearchSpec | null
  filters?: FilterSet | null
  ordering?: string | null
  page?: number
  page_size?: DocumentPageSize
}

/** How a value must be collected for a given field/operator pair. */
export type ValueShape =
  | 'none'
  | 'text'
  | 'integer'
  | 'float'
  | 'decimal'
  | 'date'
  | 'boolean'
  | 'select_option'
  | 'reference_id'

export type FieldType =
  | 'text'
  | 'long_text'
  | 'url'
  | 'monetary'
  | 'date'
  | 'datetime'
  | 'integer'
  | 'float'
  | 'boolean'
  | 'select'
  | 'document_link'
  | 'reference'
  | 'tag_set'

export interface OperatorCapability {
  operator: FilterOperator
  label: string
  value_shape: ValueShape
  multi: boolean
  /** A caveat about what this really does - e.g. that text equality is
   *  case-insensitive, or that monetary comparison ignores the currency. */
  note: string | null
}

export interface SelectOptionCapability {
  /** What the filter stores and compares. */
  id: string
  /** Display only. A rename must not change the filter's meaning. */
  label: string
}

export interface FieldCapability {
  key: string
  label: string
  field_type: FieldType
  source: 'core' | 'custom_field'
  custom_field_id: number | null
  reference_kind: string | null
  select_options: SelectOptionCapability[]
  operators: OperatorCapability[]
}

/** Which tree shapes the backend compiler can translate. */
export interface GroupingCapabilities {
  and_supported: boolean
  or_custom_fields_supported: boolean
  or_core_fields_supported: boolean
  or_mixed_supported: boolean
  not_supported: boolean
  max_conditions: number
  max_depth: number
  custom_field_max_depth: number
  custom_field_max_conditions: number
}

export interface SearchModeCapability {
  mode: SearchMode
  label: string
  description: string
}

export interface FilterCapabilities {
  fields: FieldCapability[]
  grouping: GroupingCapabilities
  search_modes: SearchModeCapability[]
  operator_semantics: Record<string, string>
}

export type FilterIssueStage = 'validation' | 'compilation'

export interface FilterIssue {
  stage: FilterIssueStage
  code: string
  /** Dotted path to the offending node, e.g. `root.children[1]`. */
  path: string
  message: string
  field: string | null
  operator: string | null
  details: Record<string, unknown> | null
}

export interface CompiledQuery {
  params: Record<string, string>
  custom_field_expression: unknown
}

/**
 * The two verdicts, deliberately separate.
 *
 * `valid: true, compilable: false` is a real and common answer: the filter is
 * well-formed, but Paperless has no way to express it (an OR across a core
 * field and a custom field, say). That is not the user having made a mistake,
 * and the UI says so differently.
 */
export interface FilterValidationResponse {
  valid: boolean
  compilable: boolean
  issues: FilterIssue[]
  compiled: CompiledQuery | null
}

export interface FilterCountResponse {
  count: number
  compiled: CompiledQuery
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

export interface StoragePathDefinition {
  id: number
  name: string
  slug: string
  path: string | null
}
