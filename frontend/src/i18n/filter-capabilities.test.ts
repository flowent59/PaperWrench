import { afterEach, describe, expect, it } from 'vitest'

import type { FilterCapabilities } from '@/api/types'

import { localizeFilterCapabilities } from './filter-capabilities'
import { setLocale } from './messages'

const capabilities: FilterCapabilities = {
  fields: [{
    key: 'core:title', label: 'Title', field_type: 'text', source: 'core',
    custom_field_id: null, reference_kind: null, select_options: [],
    operators: [{
      operator: 'contains', label: 'contains', value_shape: 'text', multi: false,
      note: 'Case-insensitive substring match.',
    }],
  }, {
    key: 'custom_field:7', label: 'Période', field_type: 'select', source: 'custom_field',
    custom_field_id: 7, reference_kind: null,
    select_options: [{ id: 'approved', label: 'Approuvé' }],
    operators: [{ operator: 'equals', label: 'is', value_shape: 'select_option', multi: false, note: null }],
  }],
  grouping: {
    and_supported: true, or_custom_fields_supported: true, or_core_fields_supported: false,
    or_mixed_supported: false, not_supported: false, max_conditions: 20, max_depth: 2,
    custom_field_max_depth: 2, custom_field_max_conditions: 20,
  },
  search_modes: [{ mode: 'title', label: 'Title', description: 'Search titles.' }],
  operator_semantics: {},
}

describe('filter capability localization', () => {
  afterEach(() => setLocale('en'))

  it('translates stable PaperWrench labels but preserves Paperless-owned values', () => {
    setLocale('fr')
    const localized = localizeFilterCapabilities(capabilities)

    expect(localized?.fields[0]).toMatchObject({ label: 'Titre' })
    expect(localized?.fields[0]?.operators[0]).toMatchObject({
      label: 'contient', note: 'Recherche de sous-chaîne insensible à la casse.',
    })
    expect(localized?.fields[1]).toMatchObject({
      label: 'Période', select_options: [{ id: 'approved', label: 'Approuvé' }],
    })
    expect(localized?.search_modes[0]).toMatchObject({ label: 'Titre' })
    expect(capabilities.fields[0]?.label).toBe('Title')
  })
})
