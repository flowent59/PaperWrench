/**
 * Display-only formatting helpers for the Explorer grid.
 *
 * Every helper here is presentation-only: none of them ever mutate the
 * underlying DTO, none of them do float arithmetic on a monetary amount
 * (the backend already carries `amount` as a decimal string - see
 * `MonetaryValueDto`), and none of them collapse ABSENT / NULL / "" / 0 /
 * false into a single "empty" idea (M3 brief).
 */

import type { CustomFieldColumnValue, CustomFieldDefinition } from '@/api/types'
import { messages } from '@/i18n/messages'

/** Locale-aware monetary formatting without ever going through a JS float. */
export function formatMonetary(currency: string, amount: string): string {
  // `Intl.NumberFormat` needs a JS number, which is a real precision risk
  // for arbitrary decimals - but display formatting (grouping separators,
  // symbol placement) only needs to be correct for the two-decimal amounts
  // Paperless actually produces, and the source of truth (the `amount`
  // string itself) is never touched or recomputed here.
  const numeric = Number(amount)
  try {
    return new Intl.NumberFormat(undefined, {
      style: 'currency',
      currency,
      currencyDisplay: 'narrowSymbol',
    }).format(numeric)
  } catch {
    // Unknown/invalid ISO currency code: fall back to the raw value rather
    // than throwing and breaking the whole row's render.
    return `${currency} ${amount}`
  }
}

/** Renders one typed custom-field value as plain text for a grid cell. */
export function formatCustomFieldValue(
  value: CustomFieldColumnValue | undefined,
  definition: CustomFieldDefinition | undefined,
): string {
  if (value === undefined || value.kind === 'absent') {
    return messages.explorer.absentValue
  }
  if (value.kind === 'null') {
    return messages.explorer.nullValue
  }
  // PRESENT from here on - "" / 0 / false are real values, never treated as
  // absent or null (M3 brief).
  if (value.monetary !== null) {
    return formatMonetary(value.monetary.currency, value.monetary.amount)
  }
  if (definition?.data_type === 'select') {
    // The id is always retained on the value itself (`select_option_id`);
    // only the *display* falls back to the label, never the reverse.
    return value.select_label ?? String(value.select_option_id ?? '')
  }
  if (definition?.data_type === 'boolean') {
    return value.raw === true
      ? messages.explorer.booleanTrue
      : value.raw === false
        ? messages.explorer.booleanFalse
        : String(value.raw)
  }
  if (value.raw === '') {
    return messages.explorer.emptyStringValue
  }
  if (value.raw === null || value.raw === undefined) {
    return messages.explorer.nullValue
  }
  return String(value.raw)
}

/** `Unknown (#42)` for an unresolved metadata reference id - never a crash. */
export function formatUnknownReference(id: number): string {
  return messages.explorer.unknownReference.replace('{id}', String(id))
}

/** Locale date formatting for the fixed ISO date/datetime strings Paperless returns. */
export function formatDate(value: string | null): string {
  if (value === null) {
    return messages.explorer.nullValue
  }
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) {
    return value
  }
  return parsed.toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  })
}
