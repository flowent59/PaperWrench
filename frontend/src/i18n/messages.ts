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
    incompatible: 'Incompatible',
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
    paperlessVersion: 'Paperless version',
    documents: 'Documents',
    maxApiVersion: 'Highest API version',
    url: 'URL',
  },
  dashboard: {
    title: 'Dashboard',
    subtitle: 'Overview of your Paperless-ngx library and PaperWrench activity.',
    notConfiguredTitle: 'Paperless-ngx is not configured',
    notConfiguredBody:
      'Set PAPERLESS_URL and PAPERLESS_TOKEN, then restart PaperWrench. The token is read from the environment and is never stored in the database nor sent to the browser.',
    unreachableTitle: 'Paperless-ngx cannot be reached',
    incompatibleTitle: 'Paperless-ngx API version is not supported',
  },
  placeholder: {
    comingSoon: 'Coming soon',
    milestone: 'This screen is delivered in a later milestone.',
  },
  /**
   * Filter Builder strings.
   *
   * Note what is NOT here: operator labels, field labels and the caveats
   * shown beside an operator all come from `GET /filters/capabilities`.
   * They describe the backend compiler's behaviour, so the backend words
   * them - duplicating them here would create a second copy to drift.
   */
  filters: {
    title: 'Filters',
    show: 'Filters',
    hide: 'Hide filters',
    where: 'Where',
    and: 'And',
    or: 'Or',
    field: 'Field',
    operator: 'Condition',
    addCondition: 'Add condition',
    addOrGroup: 'Add "any of" group',
    addAlternative: 'Add alternative',
    removeCondition: 'Remove condition',
    clearAll: 'Clear filters',
    anyOfTheFollowing: 'Any of the following:',
    orGroupCustomFieldsOnly:
      'An "any of" group can only combine custom fields - Paperless has no way to express OR across core document fields.',
    unsupportedNode: 'This filter contains a condition this version cannot display or run.',
    chooseValue: 'Choose...',
    valueListPlaceholder: 'Comma-separated values',
    amountPlaceholder: 'e.g. 0.00',
    booleanTrue: 'Yes',
    booleanFalse: 'No',
    matching: '{count} documents match',
    matchingUnknown: 'Match count unavailable',
    counting: 'Counting...',
    valid: 'Filter is valid',
    notCompilable: 'Paperless cannot run this filter',
    notCompilableBody:
      'The filter itself is fine - Paperless has no way to express this exact question, so PaperWrench will not run an approximation of it.',
    invalid: 'Filter is not valid',
    noFilters: 'No filters. Every document matches.',
    searchMode: 'Search in',
    compiledQuery: 'Query sent to Paperless',
    showCompiled: 'Show query',
    hideCompiled: 'Hide query',
  },
  explorer: {
    title: 'Explorer',
    subtitle: 'Browse your Paperless-ngx library. Read-only: nothing here writes to Paperless.',
    searchPlaceholder: 'Search by title...',
    columnTitle: 'Title',
    columnDocumentType: 'Document type',
    columnCorrespondent: 'Correspondent',
    columnCreated: 'Created',
    columnModified: 'Modified',
    columnAdded: 'Added',
    columnTags: 'Tags',
    columnArchiveSerialNumber: 'ASN',
    columns: 'Columns',
    noColumnsHidden: 'All columns are shown.',
    selectAll: 'Select all on this page',
    selectRow: 'Select row',
    selectedCount: '{count} selected',
    clearSelection: 'Clear selection',
    unknownReference: 'Unknown (#{id})',
    empty: 'No documents',
    emptyBody: 'No documents match the current search and filters.',
    loading: 'Loading documents...',
    errorTitle: 'Could not load documents',
    retry: 'Retry',
    page: 'Page',
    of: 'of',
    pageSize: 'Rows per page',
    previousPage: 'Previous page',
    nextPage: 'Next page',
    firstPage: 'First page',
    lastPage: 'Last page',
    totalDocuments: '{count} documents',
    sortAscending: 'Sorted ascending',
    sortDescending: 'Sorted descending',
    notSorted: 'Not sorted',
    absentValue: '—',
    nullValue: '—',
    emptyStringValue: '(empty)',
    booleanTrue: 'Yes',
    booleanFalse: 'No',
    filterDocumentType: 'Document type',
    filterCorrespondent: 'Correspondent',
    filterTag: 'Tag',
    filterAll: 'All',
    resetFilters: 'Reset filters',
    showingCount: 'Showing {count} of {total}',
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
