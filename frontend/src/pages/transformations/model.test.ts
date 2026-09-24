import { describe, expect, it } from 'vitest'

import type { CustomFieldDefinition } from '@/api/types'

import { emptyOperation, placeholders, serializeOperation, serializeTransformation } from './model'

const fields: CustomFieldDefinition[] = [
  { id: 7, name: 'Période concernée', data_type: 'string', extra_data: {} },
  { id: 8, name: 'Validé', data_type: 'boolean', extra_data: {} },
  { id: 9, name: 'Montant', data_type: 'monetary', extra_data: {} },
  { id: 10, name: 'Choice', data_type: 'select',
    extra_data: { select_options: [{ id: 'opaque', label: 'Été' }] } },
]

describe('M6 authoring serialization', () => {
  it('uses stable custom IDs for Unicode template bindings', () => {
    const draft = { ...emptyOperation(), bindings: { 'Période concernée': 'custom_field:7' } }
    expect(placeholders(draft.template)).toEqual(['Période concernée'])
    expect(serializeOperation(draft, fields)).toEqual({
      operation: 'template', field: { source: 'core', name: 'title' },
      template: 'Relevé de vacations – {Période concernée}',
      bindings: { 'Période concernée': {
        source: 'custom_field', field_id: 7, display_name: 'Période concernée',
      } },
    })
  })

  it('keeps false, money strings and select IDs intact', () => {
    expect(serializeOperation({ ...emptyOperation('custom_field:8'), operation: 'set', value: 'false' }, fields))
      .toMatchObject({ value: false })
    expect(serializeOperation({ ...emptyOperation('custom_field:9'), operation: 'set', value: 'EUR0.00' }, fields))
      .toMatchObject({ value: 'EUR0.00' })
    expect(serializeOperation({ ...emptyOperation('custom_field:10'), operation: 'set', value: 'opaque' }, fields))
      .toMatchObject({ value: 'opaque' })
  })

  it('distinguishes target source and clear state', () => {
    const clear = { ...emptyOperation('custom_field:7'), operation: 'clear' as const, clearState: 'null' as const }
    const filters = { root: { kind: 'group' as const, operator: 'and' as const, children: [] } }
    expect(serializeTransformation('ids', '1, 2', null, filters, [clear], fields)).toMatchObject({
      targets: { source: 'ids', document_ids: [1, 2] },
      operations: [{ state: 'null' }],
    })
    expect(serializeTransformation('dataset', '', { mode: 'title', text: 'été' }, filters, [clear], fields))
      .toMatchObject({ targets: { source: 'dataset', query: { search: { text: 'été' }, filters } } })
    expect(() => serializeTransformation('ids', '0', null, filters, [clear], fields)).toThrow()
  })

  it('rejects unknown bindings and invalid integer input before request', () => {
    expect(() => serializeOperation(emptyOperation(), fields)).toThrow()
    expect(() => serializeOperation({ ...emptyOperation('core:correspondent'), operation: 'set', value: 'abc' }, fields)).toThrow()
  })
})
