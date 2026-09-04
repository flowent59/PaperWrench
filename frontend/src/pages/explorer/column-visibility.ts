/**
 * Column visibility, persisted to `localStorage` (M3: not SQLite - this is
 * a pure display preference, not durable application state).
 */

import * as React from 'react'
import type { VisibilityState } from '@tanstack/react-table'

const STORAGE_KEY = 'paperwrench.explorer.columnVisibility'

function readInitial(): VisibilityState {
  if (typeof window === 'undefined') return {}
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY)
    if (stored === null) return {}
    const parsed: unknown = JSON.parse(stored)
    if (typeof parsed !== 'object' || parsed === null) return {}
    return parsed as VisibilityState
  } catch {
    // Corrupted/blocked storage: fall back to "everything visible".
    return {}
  }
}

export function usePersistedColumnVisibility(): [
  VisibilityState,
  React.Dispatch<React.SetStateAction<VisibilityState>>,
] {
  const [visibility, setVisibility] = React.useState<VisibilityState>(readInitial)

  React.useEffect(() => {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(visibility))
    } catch {
      // Best-effort only.
    }
  }, [visibility])

  return [visibility, setVisibility]
}
