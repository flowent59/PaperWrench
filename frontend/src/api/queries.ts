import { keepPreviousData, useQuery, type UseQueryResult } from '@tanstack/react-query'

import { documentsApi, metadataApi, systemApi } from './client'
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

export const queryKeys = {
  health: ['system', 'health'] as const,
  info: ['system', 'info'] as const,
  paperless: ['system', 'paperless'] as const,
  documents: (params: ListDocumentsParams) => ['documents', params] as const,
  tags: ['metadata', 'tags'] as const,
  correspondents: ['metadata', 'correspondents'] as const,
  documentTypes: ['metadata', 'document-types'] as const,
  customFields: ['metadata', 'custom-fields'] as const,
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

export function useDocuments(params: ListDocumentsParams): UseQueryResult<DocumentPage> {
  return useQuery({
    queryKey: queryKeys.documents(params),
    queryFn: () => documentsApi.list(params),
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

export function useCustomFields(): UseQueryResult<CustomFieldDefinition[]> {
  return useQuery({
    queryKey: queryKeys.customFields,
    queryFn: metadataApi.customFields,
    staleTime: 60_000,
  })
}
