/**
 * The value control for one condition, chosen by the backend's `value_shape`.
 *
 * There is deliberately no client-side type checking beyond what the input
 * element itself enforces. The backend is authoritative (`/filters/validate`
 * runs on every change), and duplicating its rules here would create a second
 * copy to drift. What this file does is pick the *right control*, so most
 * mistakes are simply not typeable: a date field gets a date picker, a select
 * field gets its real options, a boolean gets true/false.
 *
 * The one thing it is careful about is monetary. The input stays a text field
 * carrying a string all the way to the backend - never a `number` input,
 * whose value would arrive as a JS float and lose the exactness the whole
 * Decimal path exists to protect.
 */

import type { FieldCapability, OperatorCapability } from '@/api/types'
import { messages } from '@/i18n/messages'

export interface ReferenceOption {
  id: number
  name: string
}

interface ValueInputProps {
  field: FieldCapability
  operator: OperatorCapability
  value: unknown
  onChange: (value: unknown) => void
  /** Options for a `reference_id` shape, keyed by the field's reference_kind. */
  referenceOptions: ReferenceOption[]
  disabled?: boolean
}

const INPUT_CLASS =
  'h-9 min-w-40 rounded-md border border-input bg-background px-2 text-sm focus-ring'

export function ValueInput({
  field,
  operator,
  value,
  onChange,
  referenceOptions,
  disabled,
}: ValueInputProps) {
  // Operators like `is missing` take no value at all: showing a disabled
  // input would suggest one is expected.
  if (operator.value_shape === 'none') return null

  const label = `${field.label} ${operator.label}`

  if (operator.multi) {
    const selected = Array.isArray(value) ? (value as unknown[]) : []
    if (operator.value_shape === 'reference_id') {
      return (
        <select
          multiple
          className="min-h-20 min-w-40 rounded-md border border-input bg-background px-2 py-1 text-sm focus-ring"
          value={selected.map(String)}
          disabled={disabled}
          aria-label={label}
          onChange={(event) =>
            onChange(
              Array.from(event.target.selectedOptions).map((option) => Number(option.value)),
            )
          }
        >
          {referenceOptions.map((option) => (
            <option key={option.id} value={option.id}>
              {option.name}
            </option>
          ))}
        </select>
      )
    }
    if (operator.value_shape === 'select_option') {
      return (
        <select
          multiple
          className="min-h-20 min-w-40 rounded-md border border-input bg-background px-2 py-1 text-sm focus-ring"
          value={selected.map(String)}
          disabled={disabled}
          aria-label={label}
          onChange={(event) =>
            onChange(Array.from(event.target.selectedOptions).map((option) => option.value))
          }
        >
          {field.select_options.map((option) => (
            <option key={option.id} value={option.id}>
              {option.label}
            </option>
          ))}
        </select>
      )
    }
    // A comma-separated list for the remaining multi shapes (text `in`).
    return (
      <input
        type="text"
        className={INPUT_CLASS}
        value={selected.join(', ')}
        disabled={disabled}
        aria-label={label}
        placeholder={messages.filters.valueListPlaceholder}
        onChange={(event) =>
          onChange(
            event.target.value
              .split(',')
              .map((part) => part.trim())
              .filter((part) => part !== ''),
          )
        }
      />
    )
  }

  switch (operator.value_shape) {
    case 'boolean':
      return (
        <select
          className={INPUT_CLASS}
          value={value === true ? 'true' : 'false'}
          disabled={disabled}
          aria-label={label}
          onChange={(event) => onChange(event.target.value === 'true')}
        >
          <option value="true">{messages.filters.booleanTrue}</option>
          <option value="false">{messages.filters.booleanFalse}</option>
        </select>
      )

    case 'select_option':
      return (
        <select
          className={INPUT_CLASS}
          value={typeof value === 'string' ? value : ''}
          disabled={disabled}
          aria-label={label}
          onChange={(event) => onChange(event.target.value)}
        >
          {/* The option id is the value; the label is display only, so
              renaming an option in Paperless cannot change what this
              filter means. */}
          {field.select_options.map((option) => (
            <option key={option.id} value={option.id}>
              {option.label}
            </option>
          ))}
        </select>
      )

    case 'reference_id':
      return (
        <select
          className={INPUT_CLASS}
          value={typeof value === 'number' ? String(value) : ''}
          disabled={disabled}
          aria-label={label}
          onChange={(event) =>
            onChange(event.target.value === '' ? null : Number(event.target.value))
          }
        >
          <option value="">{messages.filters.chooseValue}</option>
          {referenceOptions.map((option) => (
            <option key={option.id} value={option.id}>
              {option.name}
            </option>
          ))}
        </select>
      )

    case 'date':
      return (
        <input
          type="date"
          className={INPUT_CLASS}
          value={typeof value === 'string' ? value : ''}
          disabled={disabled}
          aria-label={label}
          onChange={(event) => onChange(event.target.value === '' ? null : event.target.value)}
        />
      )

    case 'integer':
    case 'float':
      return (
        <input
          type="number"
          step={operator.value_shape === 'integer' ? 1 : 'any'}
          className={INPUT_CLASS}
          value={typeof value === 'number' ? String(value) : ''}
          disabled={disabled}
          aria-label={label}
          onChange={(event) =>
            onChange(event.target.value === '' ? null : Number(event.target.value))
          }
        />
      )

    case 'decimal':
      return (
        <input
          // Deliberately `text`, not `number`: a monetary amount travels as a
          // string so it reaches the backend's Decimal parser with its digits
          // intact. A number input would hand us a JS float.
          type="text"
          inputMode="decimal"
          className={INPUT_CLASS}
          value={typeof value === 'string' ? value : ''}
          disabled={disabled}
          aria-label={label}
          placeholder={messages.filters.amountPlaceholder}
          onChange={(event) => onChange(event.target.value === '' ? null : event.target.value)}
        />
      )

    default:
      return (
        <input
          type="text"
          className={INPUT_CLASS}
          value={typeof value === 'string' ? value : ''}
          disabled={disabled}
          aria-label={label}
          onChange={(event) => onChange(event.target.value === '' ? null : event.target.value)}
        />
      )
  }
}
