import { readdirSync, readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join, relative } from 'node:path'

const REQUIRED_LOCALES = ['en', 'fr']
const REQUIRED_PLURAL_FORMS = ['one', 'other']
const PLACEHOLDER = /\{([A-Za-z][A-Za-z0-9_]*)\}/g

function isObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function placeholders(value, path, errors) {
  const names = [...value.matchAll(PLACEHOLDER)].map((match) => match[1])
  if (value.replace(PLACEHOLDER, '').match(/[{}]/)) {
    errors.push(`${path}: malformed placeholder; use {name}`)
  }
  return [...new Set(names)].sort()
}

function compareKeys(reference, candidate, path, errors) {
  const expected = Object.keys(reference).sort()
  const actual = Object.keys(candidate).sort()
  for (const key of expected.filter((key) => !actual.includes(key))) errors.push(`${path}.${key}: missing key`)
  for (const key of actual.filter((key) => !expected.includes(key))) errors.push(`${path}.${key}: extra key`)
  return expected.filter((key) => actual.includes(key))
}

function compareString(reference, candidate, path, errors) {
  if (typeof candidate !== 'string') {
    errors.push(`${path}: expected a string, got ${Array.isArray(candidate) ? 'array' : typeof candidate}`)
    return
  }
  const expected = placeholders(reference, `${path} (reference)`, errors)
  const actual = placeholders(candidate, path, errors)
  if (expected.join('\0') !== actual.join('\0')) {
    errors.push(`${path}: placeholder mismatch; expected [${expected.join(', ')}], got [${actual.join(', ')}]`)
  }
}

function compareNode(reference, candidate, path, errors) {
  if (typeof reference === 'string') {
    compareString(reference, candidate, path, errors)
    return
  }
  if (!isObject(reference)) {
    errors.push(`${path}: unsupported reference value`)
    return
  }
  if (!isObject(candidate)) {
    errors.push(`${path}: expected an object`)
    return
  }

  if (reference._type === 'plural') {
    if (candidate._type !== 'plural') errors.push(`${path}: expected a plural descriptor`)
    const allowed = ['_type', ...REQUIRED_PLURAL_FORMS]
    for (const key of allowed.filter((key) => !(key in candidate))) errors.push(`${path}.${key}: missing plural form`)
    for (const key of Object.keys(candidate).filter((key) => !allowed.includes(key))) errors.push(`${path}.${key}: invalid plural form`)
    for (const form of REQUIRED_PLURAL_FORMS) {
      if (typeof reference[form] === 'string') compareString(reference[form], candidate[form], `${path}.${form}`, errors)
    }
    return
  }

  if (reference._type === 'lookup') {
    if (candidate._type !== 'lookup') errors.push(`${path}: expected a lookup descriptor`)
    const keys = compareKeys(reference, candidate, path, errors)
    for (const key of keys) {
      if (key === '_type') continue
      compareNode(reference[key], candidate[key], `${path}.${key}`, errors)
    }
    return
  }

  const keys = compareKeys(reference, candidate, path, errors)
  for (const key of keys) compareNode(reference[key], candidate[key], `${path}.${key}`, errors)
}

export function validateCatalogs(catalogs, referenceLocale = 'en') {
  const errors = []
  const reference = catalogs[referenceLocale]
  if (!isObject(reference)) return [`${referenceLocale}: reference catalogue is missing or invalid`]
  for (const [locale, catalogue] of Object.entries(catalogs)) {
    if (locale === referenceLocale) continue
    if (!isObject(catalogue)) {
      errors.push(`${locale}: catalogue is missing or invalid`)
      continue
    }
    compareNode(reference, catalogue, locale, errors)
  }
  return errors
}

export function loadCatalogs(root) {
  const catalogues = {}
  const referenceDirectory = join(root, 'en')
  const expectedDomains = readdirSync(referenceDirectory).filter((name) => name.endsWith('.json')).sort()

  for (const locale of REQUIRED_LOCALES) {
    const directory = join(root, locale)
    const domains = readdirSync(directory).filter((name) => name.endsWith('.json')).sort()
    const catalogue = {}
    for (const missing of expectedDomains.filter((name) => !domains.includes(name))) {
      catalogue[missing.replace(/\.json$/, '')] = undefined
    }
    for (const domain of domains) {
      const path = join(directory, domain)
      try {
        catalogue[domain.replace(/\.json$/, '')] = JSON.parse(readFileSync(path, 'utf8'))
      } catch (error) {
        throw new Error(`${relative(root, path)}: malformed JSON: ${error.message}`)
      }
    }
    catalogues[locale] = catalogue
  }
  return catalogues
}

const invokedPath = process.argv[1]
if (invokedPath && fileURLToPath(import.meta.url) === invokedPath) {
  const root = join(dirname(fileURLToPath(import.meta.url)), '..', 'src', 'i18n', 'locales')
  try {
    const errors = validateCatalogs(loadCatalogs(root))
    if (errors.length > 0) {
      console.error(`Translation validation failed (${errors.length} error${errors.length === 1 ? '' : 's'}):`)
      for (const error of errors) console.error(`- ${error}`)
      process.exitCode = 1
    } else {
      console.log('Translation catalogues are complete: EN/FR keys, placeholders and plural forms match.')
    }
  } catch (error) {
    console.error(error.message)
    process.exitCode = 1
  }
}
