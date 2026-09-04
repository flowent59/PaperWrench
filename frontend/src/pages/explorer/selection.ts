/**
 * Cross-page selection state for the Explorer grid.
 *
 * Selection is keyed by the Paperless document id (a stable identity)
 * rather than by row index, so it survives page/sort/search changes. This
 * deliberately implements only select-one / select-several / select-
 * current-page - never "select all N matching documents" (M3 brief: that
 * would require a fetch-all pattern this milestone explicitly forbids).
 */

import * as React from 'react'

export interface SelectionState {
  selected: ReadonlySet<number>
  isSelected: (id: number) => boolean
  toggle: (id: number) => void
  selectMany: (ids: number[]) => void
  deselectMany: (ids: number[]) => void
  clear: () => void
  count: number
}

export function useDocumentSelection(): SelectionState {
  const [selected, setSelected] = React.useState<ReadonlySet<number>>(() => new Set())

  const toggle = React.useCallback((id: number) => {
    setSelected((current) => {
      const next = new Set(current)
      if (next.has(id)) {
        next.delete(id)
      } else {
        next.add(id)
      }
      return next
    })
  }, [])

  const selectMany = React.useCallback((ids: number[]) => {
    setSelected((current) => {
      const next = new Set(current)
      for (const id of ids) next.add(id)
      return next
    })
  }, [])

  const deselectMany = React.useCallback((ids: number[]) => {
    setSelected((current) => {
      const next = new Set(current)
      for (const id of ids) next.delete(id)
      return next
    })
  }, [])

  const clear = React.useCallback(() => setSelected(new Set()), [])

  const isSelected = React.useCallback((id: number) => selected.has(id), [selected])

  return {
    selected,
    isSelected,
    toggle,
    selectMany,
    deselectMany,
    clear,
    count: selected.size,
  }
}
