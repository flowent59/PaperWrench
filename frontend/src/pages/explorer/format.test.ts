import { describe, expect, it } from 'vitest'

import type { CustomFieldColumnValue, CustomFieldDefinition } from '@/api/types'

import { formatCustomFieldValue, formatDate, formatMonetary, formatUnknownReference } from './format'

function value(overrides: Partial<CustomFieldColumnValue>): CustomFieldColumnValue {
  return {
    field_id: 1,
    kind: 'present',
    raw: null,
    monetary: null,
    select_option_id: null,
    select_label: null,
    ...overrides,
  }
}

const stringField: CustomFieldDefinition = {
  id: 1,
  name: 'Reference',
  data_type: 'string',
  extra_data: {},
}

describe('formatMonetary', () => {
  it('formats a decimal string as a currency without going through the source value', () => {
    const result = formatMonetary('EUR', '450.00')
    expect(result).toContain('450')
  })

  it('falls back to a plain string for an unrecognised currency code', () => {
    const result = formatMonetary('NOTACODE', '10.00')
    expect(result).toBe('NOTACODE 10.00')
  })
})

describe('formatCustomFieldValue', () => {
  it('renders ABSENT distinctly from NULL', () => {
    expect(formatCustomFieldValue(value({ kind: 'absent' }), stringField)).toBe('—')
    expect(formatCustomFieldValue(value({ kind: 'null' }), stringField)).toBe('—')
  })

  it('renders an empty string distinctly from null/absent', () => {
    expect(formatCustomFieldValue(value({ kind: 'present', raw: '' }), stringField)).toBe(
      '(empty)',
    )
  })

  it('renders a present monetary EUR0.00 value, never confused with null/absent', () => {
    const v = value({
      kind: 'present',
      raw: 'EUR0.00',
      monetary: { currency: 'EUR', amount: '0.00' },
    })
    const result = formatCustomFieldValue(v, {
      id: 2,
      name: 'Montant',
      data_type: 'monetary',
      extra_data: {},
    })
    expect(result).not.toBe('—')
    expect(result).toContain('0')
  })

  it('renders a select value by its label while the id remains on the value object', () => {
    const v = value({
      kind: 'present',
      raw: 'opt-1',
      select_option_id: 'opt-1',
      select_label: 'Approved',
    })
    const definition: CustomFieldDefinition = {
      id: 3,
      name: 'Status',
      data_type: 'select',
      extra_data: {},
    }
    expect(formatCustomFieldValue(v, definition)).toBe('Approved')
    expect(v.select_option_id).toBe('opt-1')
  })

  it('renders a boolean false distinctly from null/absent', () => {
    const v = value({ kind: 'present', raw: false })
    const definition: CustomFieldDefinition = {
      id: 4,
      name: 'Validated',
      data_type: 'boolean',
      extra_data: {},
    }
    const result = formatCustomFieldValue(v, definition)
    expect(result).not.toBe('—')
  })

  it('returns the absent placeholder when no value entry exists for a definition', () => {
    expect(formatCustomFieldValue(undefined, stringField)).toBe('—')
  })
})

describe('formatUnknownReference', () => {
  it('renders "Unknown (#id)"', () => {
    expect(formatUnknownReference(42)).toBe('Unknown (#42)')
  })
})

describe('formatDate', () => {
  it('renders null as the null placeholder', () => {
    expect(formatDate(null)).toBe('—')
  })

  it('formats a valid ISO date', () => {
    const result = formatDate('2024-01-15T10:00:00Z')
    expect(result).not.toBe('—')
    expect(result.length).toBeGreaterThan(0)
  })

  it('falls back to the raw string for an unparsable date', () => {
    expect(formatDate('not-a-date')).toBe('not-a-date')
  })
})
