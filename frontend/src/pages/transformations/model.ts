import type {
  CustomFieldDefinition, FillState, Normalization, FieldRef, FilterSet, SearchSpec, Transformation,
  TransformationOperation,
} from '@/api/types'
import { messages } from '@/i18n/messages'

export type OperationKind = TransformationOperation['operation']

export interface OperationDraft {
  operation: OperationKind
  fieldKey: string
  value: string
  clearState: 'absent' | 'null'
  find: string
  replacement: string
  template: string
  bindings: Record<string, string>
  normalize: Normalization
  copySource: string
  when: FillState[]
  addTags: number[]
  removeTags: number[]
}

export function emptyOperation(fieldKey = 'core:title'): OperationDraft {
  return { operation: 'set', fieldKey, value: '', clearState: 'absent', find: '',
    replacement: '', template: '', bindings: {}, normalize: 'spaces', copySource: '',
    when: [], addTags: [], removeTags: [] }
}

export function fieldRef(key: string, fields: CustomFieldDefinition[]): FieldRef {
  if (key.startsWith('core:')) return { source: 'core', name: key.slice(5) }
  const fieldId = Number(key.slice('custom_field:'.length))
  const definition = fields.find((field) => field.id === fieldId)
  if (!key.startsWith('custom_field:') || !definition) throw new Error(messages.transformations.unknownField)
  return { source: 'custom_field', field_id: fieldId, display_name: definition.name }
}

export function placeholders(template: string): string[] {
  return [...new Set([...template.matchAll(/\{([^{}]+)\}/gu)].map((match) => match[1] ?? ''))]
}

function parseSetValue(draft: OperationDraft, fields: CustomFieldDefinition): unknown {
  switch (fields.data_type) {
    case 'boolean':
      if (draft.value !== 'true' && draft.value !== 'false') throw new Error(messages.transformations.chooseBoolean)
      return draft.value === 'true'
    case 'integer': {
      const number = Number(draft.value)
      if (!/^-?\d+$/u.test(draft.value) || !Number.isSafeInteger(number)) {
        throw new Error(messages.transformations.enterInteger)
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
      if (!draft.bindings[name]) throw new Error(messages.transformations.chooseBinding.replace('{name}', name))
      bindings[name] = fieldRef(draft.bindings[name], fields)
    }
    return { operation: 'template', field, template: draft.template, bindings }
  }
  if (draft.operation === 'normalize') return { operation: 'normalize', field, mode: draft.normalize }
  if (draft.operation === 'copy') {
    if (!draft.copySource) throw new Error(messages.transformations.chooseSource)
    return { operation: 'copy', field, source_field: fieldRef(draft.copySource, fields) }
  }
  if (draft.operation === 'tags_delta') {
    return { operation: 'tags_delta', field, add: draft.addTags, remove: draft.removeTags }
  }
  let value: unknown = draft.value
  if (definition) value = parseSetValue(draft, definition)
  if (field.source === 'core') {
    if (['correspondent', 'document_type', 'storage_path', 'archive_serial_number'].includes(field.name)) {
      if (!/^-?\d+$/u.test(draft.value) || !Number.isSafeInteger(Number(draft.value))) {
        throw new Error(messages.transformations.enterIntegerId)
      }
      value = Number(draft.value)
    } else if (field.name === 'tags') {
      value = draft.value.split(',').map((part) => {
        const text = part.trim()
        if (!/^\d+$/u.test(text) || !Number.isSafeInteger(Number(text))) {
          throw new Error(messages.transformations.enterTagIds)
        }
        return Number(text)
      })
    }
  }
  if (draft.operation === 'fill') {
    if (!draft.when.length) throw new Error(messages.transformations.chooseMissing)
    return { operation: 'fill', field, value, when: draft.when }
  }
  return { operation: 'set', field, value }
}

export function serializeTransformation(
  source: 'ids' | 'dataset', ids: string, search: SearchSpec | null,
  filters: FilterSet, drafts: OperationDraft[], fields: CustomFieldDefinition[],
  ordering?: string | null,
): Transformation {
  const targets = source === 'ids'
    ? { source: 'ids' as const, document_ids: ids.split(',').map((part) => Number(part.trim())) }
    : { source: 'dataset' as const, query: { search, filters, ...(ordering ? { ordering } : {}) } }
  if (source === 'ids' && (targets.source !== 'ids' || targets.document_ids.some(
    (id) => !Number.isSafeInteger(id) || id <= 0,
  ))) throw new Error(messages.transformations.enterDocumentIds)
  return { targets, operations: drafts.map((draft) => serializeOperation(draft, fields)) }
}

export function copyType(key: string, fields: CustomFieldDefinition[]): string {
  const type = key.startsWith('custom_field:')
    ? fields.find((field) => key === `custom_field:${field.id}`)?.data_type
    : ({ 'core:title': 'text', 'core:created': 'date', 'core:archive_serial_number': 'integer' } as Record<string, string>)[key]
  return type === 'string' || type === 'longtext' ? 'text' : type ?? key
}

export function fillStates(key: string, fields: CustomFieldDefinition[]): FillState[] {
  if (key.startsWith('custom_field:')) return copyType(key, fields) === 'text'
    ? ['absent', 'null', 'empty_text'] : ['absent', 'null']
  if (key === 'core:tags') return ['no_tags']
  return ['core:correspondent', 'core:document_type', 'core:storage_path', 'core:archive_serial_number'].includes(key)
    ? ['null'] : []
}
