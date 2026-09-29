import assert from 'node:assert/strict'
import { test } from 'node:test'

import { validateCatalogs } from './validate-i18n.mjs'

const valid = () => ({
  en: { app: { greeting: 'Hello {name}', files: { _type: 'plural', one: '{count} file', other: '{count} files' } } },
  fr: { app: { greeting: 'Bonjour {name}', files: { _type: 'plural', one: '{count} fichier', other: '{count} fichiers' } } },
})

test('accepts aligned keys, placeholders and plural forms', () => {
  assert.deepEqual(validateCatalogs(valid()), [])
})

test('reports missing and extra nested keys', () => {
  const catalogs = valid()
  delete catalogs.fr.app.greeting
  catalogs.fr.app.unexpected = 'surprise'
  assert.deepEqual(validateCatalogs(catalogs), [
    'fr.app.greeting: missing key',
    'fr.app.unexpected: extra key',
  ])
})

test('reports mismatched and malformed placeholders', () => {
  const catalogs = valid()
  catalogs.fr.app.greeting = 'Bonjour {prenom} et {broken-name}'
  const errors = validateCatalogs(catalogs)
  assert.ok(errors.some((error) => error.includes('malformed placeholder')))
  assert.ok(errors.some((error) => error.includes('placeholder mismatch')))
})

test('reports incomplete plural forms', () => {
  const catalogs = valid()
  delete catalogs.fr.app.files.one
  catalogs.fr.app.files.other = 'plusieurs fichiers'
  const errors = validateCatalogs(catalogs)
  assert.ok(errors.some((error) => error.includes('missing plural form')))
  assert.ok(errors.some((error) => error.includes('placeholder mismatch')))
})

test('reports unsupported plural forms', () => {
  const catalogs = valid()
  catalogs.fr.app.files.few = '{count} fichiers'
  assert.ok(validateCatalogs(catalogs).some((error) => error.includes('invalid plural form')))
})
