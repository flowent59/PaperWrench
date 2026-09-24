import type { TransformationValue } from '@/api/types'
import { messages } from '@/i18n/messages'

/** Render the staged M6 value, including its metadata snapshot, without refetching. */
export function valueText(value: TransformationValue | null): string {
  if (value === null) return messages.transformations.unavailable
  if (value.kind !== 'present') return value.kind.toUpperCase()
  if (value.raw === '') return messages.transformations.emptyString
  if (value.monetary) return `${value.monetary.currency}${value.monetary.amount}`
  if (value.select_option_id != null) {
    return value.select_label ?? messages.preview.unknownOption.replace('{id}', value.select_option_id)
  }
  return typeof value.raw === 'string' ? value.raw : JSON.stringify(value.raw)
}
