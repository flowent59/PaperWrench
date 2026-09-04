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
  DocumentPage,
  DocumentTypeDefinition,
  HealthResponse,
  InfoResponse,
  ListDocumentsParams,
  PaperlessStatusResponse,
  TagDefinition,
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

function buildQueryString(params: ListDocumentsParams): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') {
      continue
    }
    search.set(key, String(value))
  }
  const qs = search.toString()
  return qs ? `?${qs}` : ''
}

export const documentsApi = {
  // Server-side pagination only - this is the one and only entry point the
  // Explorer uses to fetch documents. There is deliberately no "fetch all
  // pages" helper here: loading the whole library into the browser is
  // exactly what M3 exists to avoid.
  list: (params: ListDocumentsParams) =>
    apiFetch<DocumentPage>(`/documents${buildQueryString(params)}`),
}

// Reference metadata (tags/correspondents/document types/custom field
// definitions) is small, changes rarely and is only used to build the
// Explorer's column definitions and filter dropdowns - never a document
// list itself, so no pagination parameters here.
export const metadataApi = {
  tags: () => apiFetch<TagDefinition[]>('/metadata/tags'),
  correspondents: () => apiFetch<CorrespondentDefinition[]>('/metadata/correspondents'),
  documentTypes: () => apiFetch<DocumentTypeDefinition[]>('/metadata/document-types'),
  customFields: () => apiFetch<CustomFieldDefinition[]>('/metadata/custom-fields'),
}
