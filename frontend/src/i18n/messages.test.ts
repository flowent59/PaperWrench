import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

import { afterEach, describe, expect, it } from 'vitest'

import { englishMessages, messages, normalizeLocale, setLocale } from './messages'
import { frenchMessages } from './fr'

afterEach(() => setLocale('en'))

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const full = join(dir, entry)
    return statSync(full).isDirectory() ? walk(full) : [full]
  })
}

describe('centralised UI strings', () => {
  it('exposes every navigation entry of the product IA', () => {
    // Guards against a screen being added to the sidebar with an inline label.
    for (const key of [
      'dashboard',
      'documents',
      'filters',
      'transformations',
      'quality',
      'duplicates',
      'extraction',
      'schemas',
      'collections',
      'analytics',
      'jobs',
      'history',
      'settings',
    ] as const) {
      expect(messages.nav[key]).toBeTruthy()
    }
  })

  it('states the non-affiliation with Paperless-ngx', () => {
    expect(messages.app.disclaimer.toLowerCase()).toContain('not affiliated')
  })

  it('switches every domain through the stable message proxy', () => {
    const jobs = messages.jobs
    setLocale('fr')
    expect(messages.auth.title).toBe('Se connecter à PaperWrench')
    expect(jobs.title).toBe('Historique')
    expect(document.documentElement.lang).toBe('fr')
  })

  it('keeps the French catalogue structurally aligned with English', () => {
    expect(Object.keys(frenchMessages)).toEqual(Object.keys(englishMessages))
    for (const domain of Object.keys(englishMessages) as (keyof typeof englishMessages)[]) {
      expect(Object.keys(frenchMessages[domain])).toEqual(Object.keys(englishMessages[domain]))
    }
  })

  it('detects French variants and falls back unsupported locales to English', () => {
    expect(normalizeLocale('fr-CA')).toBe('fr')
    expect(normalizeLocale('FR_fr')).toBe('fr')
    expect(normalizeLocale('de-DE')).toBe('en')
    expect(normalizeLocale(undefined)).toBe('en')
  })

  it('keeps components free of hardcoded JSX text', () => {
    // A crude but effective check: JSX text nodes of two or more words.
    const files = walk(join(__dirname, '..'))
      .filter((f) => f.endsWith('.tsx'))
      .filter((f) => !f.endsWith('.test.tsx'))

    const offenders: string[] = []
    for (const file of files) {
      const source = readFileSync(file, 'utf8')
      for (const match of source.matchAll(/>\s*([A-Za-z][A-Za-z' ]{6,})\s*</g)) {
        offenders.push(`${file}: ${match[1]?.trim() ?? ''}`)
      }
    }
    expect(offenders).toEqual([])
  })
})
