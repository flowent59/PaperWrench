import { keepPreviousData, useQuery, type UseQueryResult } from '@tanstack/react-query'

import { documentsApi, filtersApi, metadataApi, systemApi } from './client'
import type {
  CorrespondentDefinition,
  CustomFieldDefinition,
  DatasetPageRequest,
  DocumentPage,
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
} from './types'

export const queryKeys = {
  health: ['system', 'health'] as const,
  info: ['system', 'info'] as const,
  paperless: ['system', 'paperless'] as const,
  documents: (request: DatasetPageRequest) => ['documents', request] as const,
  tags: ['metadata', 'tags'] as const,
  correspondents: ['metadata', 'correspondents'] as const,
  documentTypes: ['metadata', 'document-types'] as const,
  storagePaths: ['metadata', 'storage-paths'] as const,
  customFields: ['metadata', 'custom-fields'] as const,
  filterCapabilities: ['filters', 'capabilities'] as const,
  filterValidation: (filters: FilterSet) => ['filters', 'validate', filters] as const,
  filterCount: (filters: FilterSet, search: SearchSpec | null) =>
    ['filters', 'count', filters, search] as const,
}

export function useHealth(): UseQueryResult<HealthResponse> {
  return useQuery({
    queryKey: queryKeys.health,
    queryFn: systemApi.health,
    // Cheap endpoint; a short poll makes a backend restart visible.
    refetchInterval: 30_000,
  })
}

export function useInfo(): UseQueryResult<InfoResponse> {
  return useQuery({
    queryKey: queryKeys.info,
    queryFn: systemApi.info,
    // Configuration only changes on restart.
    staleTime: Infinity,
  })
}

export function usePaperlessStatus(): UseQueryResult<PaperlessStatusResponse> {
  return useQuery({
    queryKey: queryKeys.paperless,
    queryFn: systemApi.paperless,
    // This one costs a real round-trip to Paperless, so it is polled far more
    // conservatively than /system/health. It is still polled, because the
    // instance can go away while PaperWrench is open.
    refetchInterval: 120_000,
    // The endpoint reports failure in its payload rather than by status code,
    // so a rejection here means the PaperWrench backend itself is unreachable
    // and retrying hard would be pointless noise.
    retry: 1,
  })
}

/**
 * One page of a dataset.
 *
 * `enabled` is how the caller withholds the request while the filter has not
 * been confirmed compilable. Firing anyway would spend a round trip to
 * collect a 422 and, worse, would briefly put an error where the grid is -
 * for a filter the user is still in the middle of building.
 */
export function useDocuments(
  request: DatasetPageRequest,
  enabled = true,
): UseQueryResult<DocumentPage> {
  return useQuery({
    queryKey: queryKeys.documents(request),
    queryFn: () => documentsApi.query(request),
    enabled,
    // Keeps the currently-rendered page visible while the next one loads,
    // instead of flashing a loading state on every page/sort/search change -
    // important for a dense, fast-feeling grid.
    placeholderData: keepPreviousData,
  })
}

// Reference metadata backing the Explorer's dynamic columns and filter
// dropdowns. Small, low-churn lists: a generous staleTime avoids refetching
// them on every navigation while still refreshing on a full page reload.
export function useTags(): UseQueryResult<TagDefinition[]> {
  return useQuery({
    queryKey: queryKeys.tags,
    queryFn: metadataApi.tags,
    staleTime: 60_000,
  })
}

export function useCorrespondents(): UseQueryResult<CorrespondentDefinition[]> {
  return useQuery({
    queryKey: queryKeys.correspondents,
    queryFn: metadataApi.correspondents,
    staleTime: 60_000,
  })
}

export function useDocumentTypes(): UseQueryResult<DocumentTypeDefinition[]> {
  return useQuery({
    queryKey: queryKeys.documentTypes,
    queryFn: metadataApi.documentTypes,
    staleTime: 60_000,
  })
}

export function useStoragePaths(): UseQueryResult<StoragePathDefinition[]> {
  return useQuery({
    queryKey: queryKeys.storagePaths,
    queryFn: metadataApi.storagePaths,
    staleTime: 60_000,
  })
}

export function useCustomFields(): UseQueryResult<CustomFieldDefinition[]> {
  return useQuery({
    queryKey: queryKeys.customFields,
    queryFn: metadataApi.customFields,
    staleTime: 60_000,
  })
}

/**
 * What this instance can be filtered on.
 *
 * The Filter Builder renders these rules rather than reimplementing them, so
 * a field added in Paperless becomes filterable without a frontend change,
 * and an operator the compiler cannot translate is never offered. Cached for
 * a while: the answer only changes when custom field definitions do.
 */
export function useFilterCapabilities(): UseQueryResult<FilterCapabilities> {
  return useQuery({
    queryKey: queryKeys.filterCapabilities,
    queryFn: filtersApi.capabilities,
    staleTime: 60_000,
  })
}

/**
 * Ask the backend whether the filter being built is valid and compilable.
 *
 * The backend is authoritative. The builder does constrain what can be
 * expressed (it only offers operators a field allows, and only custom fields
 * inside an OR group), but that is an affordance, not the rule - this is.
 * Costs no request to Paperless, so calling it as the user types is free.
 */
export function useFilterValidation(
  filters: FilterSet,
  enabled: boolean,
): UseQueryResult<FilterValidationResponse> {
  return useQuery({
    queryKey: queryKeys.filterValidation(filters),
    queryFn: () => filtersApi.validate(filters),
    enabled,
    placeholderData: keepPreviousData,
  })
}

/**
 * How many documents match - asked of Paperless, never counted in the browser.
 *
 * Disabled while the filter is not compilable, so a stale number never sits
 * next to a filter that would be refused.
 */
export function useFilterCount(
  filters: FilterSet,
  search: SearchSpec | null,
  enabled: boolean,
): UseQueryResult<FilterCountResponse> {
  return useQuery({
    queryKey: queryKeys.filterCount(filters, search),
    queryFn: () => filtersApi.count(filters, search),
    enabled,
    placeholderData: keepPreviousData,
  })
}
