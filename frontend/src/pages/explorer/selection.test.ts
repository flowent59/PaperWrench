import { act, renderHook } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { useDocumentSelection } from './selection'

describe('useDocumentSelection', () => {
  it('starts empty', () => {
    const { result } = renderHook(() => useDocumentSelection())
    expect(result.current.count).toBe(0)
  })

  it('select-one: toggling an id selects then deselects it', () => {
    const { result } = renderHook(() => useDocumentSelection())

    act(() => result.current.toggle(1))
    expect(result.current.isSelected(1)).toBe(true)
    expect(result.current.count).toBe(1)

    act(() => result.current.toggle(1))
    expect(result.current.isSelected(1)).toBe(false)
    expect(result.current.count).toBe(0)
  })

  it('select-several: multiple ids can be selected independently', () => {
    const { result } = renderHook(() => useDocumentSelection())

    act(() => {
      result.current.toggle(1)
      result.current.toggle(2)
      result.current.toggle(3)
    })

    expect(result.current.count).toBe(3)
    expect(result.current.isSelected(2)).toBe(true)
  })

  it('select-current-page: selectMany adds a whole page at once', () => {
    const { result } = renderHook(() => useDocumentSelection())

    act(() => result.current.selectMany([10, 11, 12]))

    expect(result.current.count).toBe(3)
    expect(result.current.isSelected(11)).toBe(true)
  })

  it('persists selection across a simulated page navigation (different id set)', () => {
    const { result } = renderHook(() => useDocumentSelection())

    act(() => result.current.selectMany([1, 2, 3]))
    // Simulate navigating to page 2: a disjoint set of ids is now rendered,
    // but the selection from page 1 must remain intact.
    act(() => result.current.selectMany([4, 5]))

    expect(result.current.count).toBe(5)
    expect(result.current.isSelected(1)).toBe(true)
    expect(result.current.isSelected(5)).toBe(true)
  })

  it('deselectMany removes exactly the given ids, leaving the rest selected', () => {
    const { result } = renderHook(() => useDocumentSelection())

    act(() => result.current.selectMany([1, 2, 3]))
    act(() => result.current.deselectMany([2]))

    expect(result.current.isSelected(1)).toBe(true)
    expect(result.current.isSelected(2)).toBe(false)
    expect(result.current.isSelected(3)).toBe(true)
  })

  it('clear empties the whole selection', () => {
    const { result } = renderHook(() => useDocumentSelection())

    act(() => result.current.selectMany([1, 2, 3]))
    act(() => result.current.clear())

    expect(result.current.count).toBe(0)
  })
})
