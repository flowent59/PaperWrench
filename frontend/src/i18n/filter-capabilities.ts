import type {
  FieldCapability,
  FilterCapabilities,
  OperatorCapability,
  SearchModeCapability,
} from '@/api/types'

import { messages } from './messages'

function localizeOperator(
  fieldType: FieldCapability['field_type'],
  capability: OperatorCapability,
): OperatorCapability {
  return {
    ...capability,
    label: messages.filters.operatorLabel(capability.operator, capability.label),
    note: capability.note === null
      ? null
      : messages.filters.operatorNote(fieldType, capability.operator, capability.note),
  }
}

export function localizeFieldCapability(field: FieldCapability): FieldCapability {
  return {
    ...field,
    // Custom-field names and select labels are user data from Paperless.
    label: field.source === 'core'
      ? messages.filters.coreField(field.key, field.label)
      : field.label,
    operators: field.operators.map((operator) => localizeOperator(field.field_type, operator)),
  }
}

export function localizeSearchMode(mode: SearchModeCapability): SearchModeCapability {
  return {
    ...mode,
    label: messages.filters.searchModeLabel(mode.mode, mode.label),
    description: messages.filters.searchModeDescription(mode.mode, mode.description),
  }
}

export function localizeFilterCapabilities(
  capabilities: FilterCapabilities | undefined,
): FilterCapabilities | undefined {
  if (capabilities === undefined) return undefined
  return {
    ...capabilities,
    // Some older saved/mock payloads can omit one of these collections.
    // Treat them as empty just as the consuming screens did before i18n.
    fields: (capabilities.fields ?? []).map(localizeFieldCapability),
    search_modes: (capabilities.search_modes ?? []).map(localizeSearchMode),
  }
}
