/**
 * Typed API client.
 *
 * Single-origin by design (ADR-0001): all requests are relative, so no base
 * URL, no CORS and no credentials handling in the browser. The Paperless token
 * lives only in the backend process and must never appear in a frontend
 * request.
 *
 * Every backend error uses the same envelope, so parsing happens in exactly
 * one place.
 */

import type {
  CorrespondentDefinition,
  CustomFieldDefinition,
  DatasetPageRequest,
  DocumentPage,
  DocumentDetail,
  EditRequest,
  EditResponse,
  DocumentTypeDefinition,
  FilterCapabilities,
  FilterCountResponse,
  FilterSet,
  FilterValidationResponse,
  HealthResponse,
  InfoResponse,
  PaperlessStatusResponse,
  SearchSpec,
  StoragePathDefinition,
  TagDefinition,
  Transformation,
  EvaluationResult,
  TransformationValidationResult,
  CreatedPreview,
  PreviewSummary,
  PreviewPage,
  DocumentSchema,
  SchemaDefinition,
  SchemaEvaluationPage,
  ExplicitIdsPage,
  QualityPage,
  CollectionDefinition,
  CollectionView,
  CollectionMemberPage,
} from './types'

export const API_PREFIX = '/api/v1'

export interface ApiErrorDetail {
  code: string
  message: string
  details?: Record<string, unknown> | null
}

export class ApiError extends Error {
  readonly code: string
  readonly status: number
  readonly details: Record<string, unknown> | null

  constructor(status: number, detail: ApiErrorDetail) {
    super(detail.message)
    this.name = 'ApiError'
    this.status = status
    this.code = detail.code
    this.details = detail.details ?? null
  }
}

/** Thrown when the backend cannot be reached at all (server down, DNS, offline). */
export class NetworkError extends Error {
  constructor(cause: unknown) {
    super('Could not reach the PaperWrench backend.')
    this.name = 'NetworkError'
    this.cause = cause
  }
}

async function parseError(response: Response): Promise<ApiError> {
  let detail: ApiErrorDetail = {
    code: 'INTERNAL_ERROR',
    message: `${response.status} ${response.statusText}`,
  }
  try {
    const body: unknown = await response.json()
    if (
      typeof body === 'object' &&
      body !== null &&
      'error' in body &&
      typeof (body as { error: unknown }).error === 'object'
    ) {
      detail = (body as { error: ApiErrorDetail }).error
    }
  } catch {
    // Non-JSON error body (proxy, gateway): keep the status-based fallback.
  }
  return new ApiError(response.status, detail)
}

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${API_PREFIX}${path}`, {
      ...init,
      headers: {
        Accept: 'application/json',
        ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
        ...init?.headers,
      },
    })
  } catch (cause) {
    throw new NetworkError(cause)
  }

  if (!response.ok) {
    throw await parseError(response)
  }
  if (response.status === 204) {
    return undefined as T
  }
  return (await response.json()) as T
}

export const systemApi = {
  health: () => apiFetch<HealthResponse>('/system/health'),
  info: () => apiFetch<InfoResponse>('/system/info'),
  // Always answers 200, even when Paperless is down or unconfigured: the
  // failure is described in the payload rather than thrown, so the UI can
  // render *why* it is not connected instead of a bare network error.
  paperless: () => apiFetch<PaperlessStatusResponse>('/system/paperless'),
}

export const documentsApi = {
  byIds: (documentIds: number[]) => apiFetch<ExplicitIdsPage>('/documents/by-ids', {
    method: 'POST', body: JSON.stringify({ document_ids: documentIds }),
  }),
  detail: (id: number) => apiFetch<DocumentDetail>(`/documents/${id}`),
  edit: (id: number, request: EditRequest) =>
    apiFetch<EditResponse>(`/documents/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(request),
    }),
  // Server-side pagination only - this is the one and only list entry point the
  // Explorer uses to fetch documents. There is deliberately no "fetch all
  // pages" helper here: loading the whole library into the browser is
  // exactly what M3 exists to avoid, and M4 kept that guarantee while
  // replacing the ad-hoc filter parameters with a real FilterSet.
  //
  // A POST for a read is deliberate: the filter tree is a nested structure,
  // and encoding it into a query string would make it neither readable nor
  // reliably round-trippable. It writes nothing.
  query: (request: DatasetPageRequest) =>
    apiFetch<DocumentPage>('/documents/query', {
      method: 'POST',
      body: JSON.stringify(request),
    }),
}

export const qualityApi = {
  page: (schemaId: number, page: number) =>
    apiFetch<QualityPage>(`/quality/schemas/${schemaId}?page=${page}&page_size=25`),
}

/**
 * The Filter Engine.
 *
 * `capabilities` is what keeps the Filter Builder honest: the operator lists
 * and grouping rules it renders come from the backend compiler's own tables,
 * so the UI cannot offer a filter that will be refused.
 *
 * `validate` reports rather than throws - it is called while the user is
 * still typing, and both of its verdicts are useful. `count` throws, because
 * a count is used to decide something and a misleading number is worse than
 * an error.
 */
export const filtersApi = {
  capabilities: () => apiFetch<FilterCapabilities>('/filters/capabilities'),
  validate: (filters: FilterSet) =>
    apiFetch<FilterValidationResponse>('/filters/validate', {
      method: 'POST',
      body: JSON.stringify({ filters }),
    }),
  count: (filters: FilterSet, search: SearchSpec | null) =>
    apiFetch<FilterCountResponse>('/filters/count', {
      method: 'POST',
      body: JSON.stringify({ filters, search }),
    }),
}

export const collectionsApi = {
  list: () => apiFetch<CollectionView[]>('/collections'),
  create: (data: CollectionDefinition & { document_ids: number[] }) =>
    apiFetch<CollectionView>('/collections', { method: 'POST', body: JSON.stringify(data) }),
  update: (id: number, data: CollectionDefinition) =>
    apiFetch<CollectionView>(`/collections/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  remove: (id: number) => apiFetch<void>(`/collections/${id}`, { method: 'DELETE' }),
  members: (id: number, page: number) =>
    apiFetch<CollectionMemberPage>(`/collections/${id}/documents?page=${page}&page_size=25`),
  add: (id: number, document_ids: number[]) =>
    apiFetch<CollectionView>(`/collections/${id}/documents`, {
      method: 'POST', body: JSON.stringify({ document_ids }),
    }),
  removeMembers: (id: number, document_ids: number[]) =>
    apiFetch<CollectionView>(`/collections/${id}/documents`, {
      method: 'DELETE', body: JSON.stringify({ document_ids }),
    }),
}

export const schemasApi = {
  list: () => apiFetch<DocumentSchema[]>('/schemas'),
  create: (schema: SchemaDefinition) => apiFetch<DocumentSchema>('/schemas', {
    method: 'POST', body: JSON.stringify(schema),
  }),
  update: (id: number, schema: SchemaDefinition) => apiFetch<DocumentSchema>(`/schemas/${id}`, {
    method: 'PUT', body: JSON.stringify(schema),
  }),
  remove: (id: number) => apiFetch<void>(`/schemas/${id}`, { method: 'DELETE' }),
  evaluate: (id: number, page: number) =>
    apiFetch<SchemaEvaluationPage>(`/schemas/${id}/evaluate?page=${page}&page_size=25`),
}

// Reference metadata (tags/correspondents/document types/custom field
// definitions) is small, changes rarely and is only used to build the
// Explorer's column definitions and filter dropdowns - never a document
// list itself, so no pagination parameters here.
export const metadataApi = {
  tags: () => apiFetch<TagDefinition[]>('/metadata/tags'),
  correspondents: () => apiFetch<CorrespondentDefinition[]>('/metadata/correspondents'),
  documentTypes: () => apiFetch<DocumentTypeDefinition[]>('/metadata/document-types'),
  storagePaths: () => apiFetch<StoragePathDefinition[]>('/metadata/storage-paths'),
  customFields: () => apiFetch<CustomFieldDefinition[]>('/metadata/custom-fields'),
}

export const transformationsApi = {
  validate: (transformation: Transformation) =>
    apiFetch<TransformationValidationResult>('/transformations/validate', {
      method: 'POST',
      body: JSON.stringify(transformation),
    }),
  evaluate: (documentId: number, transformation: Transformation) =>
    apiFetch<EvaluationResult>(`/transformations/documents/${documentId}/evaluate`, {
      method: 'POST',
      body: JSON.stringify(transformation),
    }),
}

export const previewsApi = {
  create: (transformation: Transformation) => apiFetch<CreatedPreview>('/previews', {
    method: 'POST', body: JSON.stringify(transformation),
  }),
  page: (id: string, page: number, status: string) => apiFetch<PreviewPage>(
    `/previews/${id}/documents?page=${page}&page_size=25${status ? `&status=${status}` : ''}`,
  ),
  confirm: (preview: CreatedPreview, transformation: Transformation) =>
    apiFetch<PreviewSummary>(`/previews/${preview.id}/confirm`, {
      method: 'POST', body: JSON.stringify({ preview_token: preview.preview_token,
        transformation, target_fingerprint: preview.target_fingerprint,
        result_fingerprint: preview.result_fingerprint, version: preview.version, acknowledge: true }),
    }),
  discard: (id: string) => apiFetch<void>(`/previews/${id}`, { method: 'DELETE' }),
}

export const jobsApi = {
  rollbackPreview: (id: number) => apiFetch<CreatedPreview>(`/jobs/${id}/rollback-preview`, { method: 'POST' }),
  rollback: (id: number, preview: CreatedPreview, acknowledgeExternalRace: boolean) =>
    apiFetch<import('./types').JobView>(`/jobs/${id}/rollback`, { method: 'POST', body: JSON.stringify({
      preview_id: preview.id, preview_token: preview.preview_token,
      target_fingerprint: preview.target_fingerprint, result_fingerprint: preview.result_fingerprint,
      version: preview.version, acknowledge: true, acknowledge_external_race: acknowledgeExternalRace,
    }) }),
  create: (preview: CreatedPreview, transformation: Transformation, acknowledgeExternalRace: boolean) =>
    apiFetch<import('./types').JobView>('/jobs', { method: 'POST', body: JSON.stringify({
      preview_id: preview.id, preview_token: preview.preview_token, transformation,
      target_fingerprint: preview.target_fingerprint, result_fingerprint: preview.result_fingerprint,
      version: preview.version, acknowledge: true, acknowledge_external_race: acknowledgeExternalRace,
    }) }),
  list: (page: number) => apiFetch<import('./types').HistoryPage<import('./types').JobView>>(`/jobs?page=${page}&page_size=25`),
  detail: (id: number) => apiFetch<import('./types').JobView>(`/jobs/${id}`),
  targets: (id: number, page: number, status: string) =>
    apiFetch<import('./types').HistoryPage<import('./types').TargetView>>(
      `/jobs/${id}/targets?page=${page}&page_size=25${status ? `&status=${status}` : ''}`),
  operations: (id: number, document: number, page: number) =>
    apiFetch<import('./types').HistoryPage<import('./types').OperationView>>(
      `/jobs/${id}/operations?document_id=${document}&page=${page}&page_size=25`),
  resume: (id: number) => apiFetch<import('./types').JobView>(`/jobs/${id}/resume`, { method: 'POST' }),
}
