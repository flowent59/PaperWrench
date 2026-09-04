import { act, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { usePersistedColumnVisibility } from './column-visibility'

const STORAGE_KEY = 'paperwrench.explorer.columnVisibility'

afterEach(() => {
  window.localStorage.clear()
})

describe('usePersistedColumnVisibility', () => {
  it('starts with everything visible when nothing is stored', () => {
    const { result } = renderHook(() => usePersistedColumnVisibility())
    expect(result.current[0]).toEqual({})
  })

  it('persists a visibility change to localStorage', () => {
    const { result } = renderHook(() => usePersistedColumnVisibility())

    act(() => result.current[1]({ tags: false }))

    expect(JSON.parse(window.localStorage.getItem(STORAGE_KEY) ?? '{}')).toEqual({
      tags: false,
    })
  })

  it('reads back a previously persisted value on next mount', () => {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ archive_serial_number: false }))

    const { result } = renderHook(() => usePersistedColumnVisibility())

    expect(result.current[0]).toEqual({ archive_serial_number: false })
  })

  it('falls back to everything visible when the stored value is corrupted', () => {
    window.localStorage.setItem(STORAGE_KEY, '{not-json')

    const { result } = renderHook(() => usePersistedColumnVisibility())

    expect(result.current[0]).toEqual({})
  })
})
