import { getLocale, type Locale } from './messages'

export function intlLocale(locale: Locale = getLocale()): string {
  return locale === 'fr' ? 'fr-FR' : 'en-GB'
}

export function formatNumber(value: number, locale: Locale = getLocale()): string {
  return new Intl.NumberFormat(intlLocale(locale)).format(value)
}

export function formatDateTime(value: string | Date, locale: Locale = getLocale()): string {
  const parsed = value instanceof Date ? value : new Date(value)
  if (Number.isNaN(parsed.getTime())) return String(value)
  return new Intl.DateTimeFormat(intlLocale(locale), {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(parsed)
}

export function formatDateValue(value: string, locale: Locale = getLocale()): string {
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value
  return new Intl.DateTimeFormat(intlLocale(locale), {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  }).format(parsed)
}

export function formatCurrency(
  currency: string,
  amount: string,
  locale: Locale = getLocale(),
): string {
  const numeric = Number(amount)
  try {
    return new Intl.NumberFormat(intlLocale(locale), {
      style: 'currency',
      currency,
      currencyDisplay: 'narrowSymbol',
    }).format(numeric)
  } catch {
    return `${currency} ${amount}`
  }
}
