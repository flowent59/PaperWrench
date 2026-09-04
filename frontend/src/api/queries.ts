import { useQuery, type UseQueryResult } from '@tanstack/react-query'

import { systemApi } from './client'
import type {
  HealthResponse,
  InfoResponse,
  PaperlessStatusResponse,
} from './types'

export const queryKeys = {
  health: ['system', 'health'] as const,
  info: ['system', 'info'] as const,
  paperless: ['system', 'paperless'] as const,
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
