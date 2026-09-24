import type {
  CustomFieldDefinition, FieldRef, FilterSet, SearchSpec, Transformation,
  TransformationOperation,
} from '@/api/types'

export type OperationKind = 'set' | 'clear' | 'replace' | 'template'

export interface OperationDraft {
  operation: OperationKind
  fieldKey: string
  value: string
  clearState: 'absent' | 'null'
  find: string
  replacement: string
  template: string
  bindings: Record<string, string>
}

export function emptyOperation(fieldKey = 'core:title'): OperationDraft {
  return { operation: 'template', fieldKey, value: '', clearState: 'absent', find: '',
    replacement: '', template: 'Relevé de vacations – {Période concernée}', bindings: {} }
}

export function fieldRef(key: string, fields: CustomFieldDefinition[]): FieldRef {
  if (key.startsWith('core:')) return { source: 'core', name: key.slice(5) }
  const fieldId = Number(key.slice('custom_field:'.length))
  const definition = fields.find((field) => field.id === fieldId)
  if (!key.startsWith('custom_field:') || !definition) throw new Error('Unknown field')
  return { source: 'custom_field', field_id: fieldId, display_name: definition.name }
}

export function placeholders(template: string): string[] {
  return [...new Set([...template.matchAll(/\{([^{}]+)\}/gu)].map((match) => match[1] ?? ''))]
}

function parseSetValue(draft: OperationDraft, fields: CustomFieldDefinition): unknown {
  switch (fields.data_type) {
    case 'boolean':
      if (draft.value !== 'true' && draft.value !== 'false') throw new Error('Choose true or false')
      return draft.value === 'true'
    case 'integer': {
      const number = Number(draft.value)
      if (!/^-?\d+$/u.test(draft.value) || !Number.isSafeInteger(number)) {
        throw new Error('Enter an integer')
      }
      return number
    }
    default:
      return draft.value
  }
}

export function serializeOperation(
  draft: OperationDraft, fields: CustomFieldDefinition[],
): TransformationOperation {
  const field = fieldRef(draft.fieldKey, fields)
  const definition = field.source === 'custom_field'
    ? fields.find((item) => item.id === field.field_id) : undefined
  if (draft.operation === 'clear') {
    return { operation: 'clear', field,
      ...(field.source === 'custom_field' ? { state: draft.clearState } : {}) }
  }
  if (draft.operation === 'replace') {
    return { operation: 'replace', field, find: draft.find, replacement: draft.replacement }
  }
  if (draft.operation === 'template') {
    const bindings: Record<string, FieldRef> = {}
    for (const name of placeholders(draft.template)) {
      if (!draft.bindings[name]) throw new Error(`Choose a field for {${name}}`)
      bindings[name] = fieldRef(draft.bindings[name], fields)
    }
    return { operation: 'template', field, template: draft.template, bindings }
  }
  let value: unknown = draft.value
  if (definition) value = parseSetValue(draft, definition)
  if (field.source === 'core') {
    if (['correspondent', 'document_type', 'storage_path', 'archive_serial_number'].includes(field.name)) {
      if (!/^-?\d+$/u.test(draft.value) || !Number.isSafeInteger(Number(draft.value))) {
        throw new Error('Enter an integer ID')
      }
      value = Number(draft.value)
    } else if (field.name === 'tags') {
      value = draft.value.split(',').map((part) => {
        const text = part.trim()
        if (!/^\d+$/u.test(text) || !Number.isSafeInteger(Number(text))) {
          throw new Error('Enter comma-separated tag IDs')
        }
        return Number(text)
      })
    }
  }
  return { operation: 'set', field, value }
}

export function serializeTransformation(
  source: 'ids' | 'dataset', ids: string, search: SearchSpec | null,
  filters: FilterSet, drafts: OperationDraft[], fields: CustomFieldDefinition[],
): Transformation {
  const targets = source === 'ids'
    ? { source: 'ids' as const, document_ids: ids.split(',').map((part) => Number(part.trim())) }
    : { source: 'dataset' as const, query: { search, filters } }
  if (source === 'ids' && (targets.source !== 'ids' || targets.document_ids.some(
    (id) => !Number.isSafeInteger(id) || id <= 0,
  ))) throw new Error('Enter positive document IDs separated by commas')
  return { targets, operations: drafts.map((draft) => serializeOperation(draft, fields)) }
}
