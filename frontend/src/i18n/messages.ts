/**
 * Centralised UI strings.
 *
 * Deliberately NOT a full i18n framework for the MVP: no plural rules, no
 * lazy-loaded catalogues, no context providers. The single rule enforced from
 * M0 is that no component contains a hardcoded user-facing string. Adding a
 * locale later means adding one object here and a language switch, without
 * touching any component.
 *
 * Note the distinction between *UI language* (English for the MVP) and *data
 * language*: document titles, custom field names such as "Periode concernee"
 * and monetary values in euros are user data and must round-trip untouched.
 */

export const messages = {
  app: {
    name: 'PaperWrench',
    tagline: 'Power tools for Paperless-ngx',
    disclaimer: 'Independent project. Not affiliated with Paperless-ngx.',
  },
  nav: {
    dashboard: 'Dashboard',
    documents: 'Documents',
    filters: 'Filter Sets',
    transformations: 'Transformations',
    quality: 'Data Quality',
    duplicates: 'Duplicates',
    extraction: 'Extraction',
    schemas: 'Schemas',
    collections: 'Collections',
    analytics: 'Analytics',
    jobs: 'Jobs',
    history: 'History',
    settings: 'Settings',
    sectionWorkspace: 'Workspace',
    sectionTools: 'Tools',
    sectionOperations: 'Operations',
    sectionSystem: 'System',
  },
  status: {
    connected: 'Connected',
    disconnected: 'Disconnected',
    notConfigured: 'Not configured',
    checking: 'Checking...',
    degraded: 'Degraded',
    unknown: 'Unknown',
  },
  system: {
    title: 'System status',
    backend: 'Backend',
    database: 'Database',
    paperless: 'Paperless-ngx',
    version: 'Version',
    apiVersion: 'API version',
    concurrency: 'Max concurrency',
    pageSize: 'Default page size',
    healthy: 'Healthy',
    unreachable: 'Unreachable',
  },
  dashboard: {
    title: 'Dashboard',
    subtitle: 'Overview of your Paperless-ngx library and PaperWrench activity.',
    notConfiguredTitle: 'Paperless-ngx is not configured',
    notConfiguredBody:
      'Set PAPERLESS_URL and PAPERLESS_TOKEN, then restart PaperWrench. The token is read from the environment and is never stored in the database nor sent to the browser.',
  },
  placeholder: {
    comingSoon: 'Coming soon',
    milestone: 'This screen is delivered in a later milestone.',
  },
  safety: {
    dryRunDefault: 'Dry Run is the default. Nothing is written without an explicit confirmation.',
    readOnlyNow: 'PaperWrench has not written anything to Paperless-ngx yet.',
  },
  theme: {
    toggle: 'Toggle theme',
    light: 'Light',
    dark: 'Dark',
  },
  errors: {
    title: 'Something went wrong',
    retry: 'Retry',
    generic: 'An unexpected error occurred.',
    network: 'Could not reach the PaperWrench backend.',
  },
} as const

export type Messages = typeof messages

/** The single accessor components are allowed to use. */
export function t(): Messages {
  return messages
}
