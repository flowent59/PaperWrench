import { useQuery, type UseQueryResult } from '@tanstack/react-query'

import { systemApi } from './client'
import type { HealthResponse, InfoResponse } from './types'

export const queryKeys = {
  health: ['system', 'health'] as const,
  info: ['system', 'info'] as const,
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
