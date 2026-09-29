import { rawCatalogs, type Locale, type RawCatalog } from './catalogs'

export type { Locale } from './catalogs'

type Variables = Readonly<Record<string, string | number>>
type PluralMessage = { readonly _type: 'plural'; readonly one: string; readonly other: string }
type LookupMessage = { readonly _type: 'lookup'; readonly values: Readonly<Record<string, string>> }

function interpolate(template: string, variables: Variables): string {
  return template.replace(/\{([A-Za-z][A-Za-z0-9_]*)\}/g, (placeholder, name: string) =>
    Object.hasOwn(variables, name) ? String(variables[name]) : placeholder)
}

function plural(message: unknown, count: number, locale: Locale, variables: Variables): string {
  const descriptor = message as PluralMessage
  const form = new Intl.PluralRules(locale).select(count) === 'one' ? descriptor.one : descriptor.other
  return interpolate(form, variables)
}

function lookup(message: unknown, key: string, fallback: string): string {
  const descriptor = message as LookupMessage
  return descriptor.values[key] ?? fallback
}

function materialize(catalog: RawCatalog, locale: Locale) {
  return {
    ...catalog,
    collections: {
      ...catalog.collections,
      count: (count: number) => plural(catalog.collections.count, count, locale, { count }),
      page: (page: number, total: number) => interpolate(catalog.collections.page, {
        page, total: Math.max(1, total),
      }),
    },
    quality: {
      ...catalog.quality,
      ruleCount: (index: number, count: number) =>
        plural(catalog.quality.ruleCount, count, locale, { index, count }),
      page: (page: number, pages: number) => interpolate(catalog.quality.page, { page, pages }),
      customField: (id: number) => interpolate(catalog.quality.customField, { id }),
      exactIdsSelection: (count: number) =>
        plural(catalog.quality.exactIdsSelection, count, locale, { count }),
      unavailable: (count: number) => plural(catalog.quality.unavailable, count, locale, { count }),
    },
    schemas: {
      ...catalog.schemas,
      matching: (total: number, page: number, pages: number) =>
        plural(catalog.schemas.matching, total, locale, { total, page, pages }),
      ruleField: (index: number) => interpolate(catalog.schemas.ruleField, { index }),
      ruleKind: (index: number) => interpolate(catalog.schemas.ruleKind, { index }),
    },
    transformations: {
      ...catalog.transformations,
      coreField: (key: string, fallback: string) => lookup(catalog.transformations.coreField, key, fallback),
    },
    filters: {
      ...catalog.filters,
      coreField: (key: string, fallback: string) => locale === 'en'
        ? fallback : lookup(catalog.filters.coreField, key, fallback),
      operatorLabel: (operator: string, fallback: string) =>
        locale === 'en' ? fallback : lookup(catalog.filters.operatorLabel, operator, fallback),
      operatorNote: (fieldType: string, operator: string, fallback: string) =>
        locale === 'en' ? fallback : lookup(catalog.filters.operatorNote, `${fieldType}:${operator}`, fallback),
      searchModeLabel: (mode: string, fallback: string) =>
        locale === 'en' ? fallback : lookup(catalog.filters.searchModeLabel, mode, fallback),
      searchModeDescription: (mode: string, fallback: string) =>
        locale === 'en' ? fallback : lookup(catalog.filters.searchModeDescription, mode, fallback),
    },
    errors: {
      ...catalog.errors,
      unknownCode: (code: string) => interpolate(catalog.errors.unknownCode, { code }),
      withHttpStatus: (message: string, status: number) =>
        interpolate(catalog.errors.withHttpStatus, { message, status }),
    },
  }
}

export const englishMessages = materialize(rawCatalogs.en, 'en')
export const frenchMessages = materialize(rawCatalogs.fr, 'fr')

type Widen<T> = T extends (...args: infer Args) => string
  ? (...args: Args) => string
  : T extends string
    ? string
    : T extends object
      ? { readonly [Key in keyof T]: Widen<T[Key]> }
      : T

export type Messages = Widen<typeof englishMessages>

const catalogs: Record<Locale, Messages> = {
  en: englishMessages,
  fr: frenchMessages,
}

export function normalizeLocale(value: string | null | undefined): Locale {
  return value?.trim().toLowerCase().split(/[-_]/, 1)[0] === 'fr' ? 'fr' : 'en'
}

let activeLocale: Locale = normalizeLocale(
  typeof navigator === 'undefined' ? undefined : navigator.language,
)

export function getLocale(): Locale {
  return activeLocale
}

export function setLocale(locale: Locale): void {
  activeLocale = locale
  if (typeof document !== 'undefined') document.documentElement.lang = locale
}

setLocale(activeLocale)

const domains = new Map<PropertyKey, object>()

/**
 * Stable proxy used by existing components. Domain proxies resolve every key
 * against the active catalogue, so a locale change plus the keyed app remount
 * updates module-level aliases such as `const m = messages.jobs` safely.
 */
export const messages = new Proxy({} as Messages, {
  get(_target, domain: keyof Messages) {
    if (!domains.has(domain)) {
      domains.set(domain, new Proxy({}, {
        get(_domainTarget, key: PropertyKey) {
          return Reflect.get(catalogs[activeLocale][domain], key)
        },
      }))
    }
    return domains.get(domain)
  },
})

/** The single accessor components are allowed to use. */
export function t(): Messages {
  return messages
}
