import enApp from './locales/en/app.json'
import enAnalytics from './locales/en/analytics.json'
import enAuth from './locales/en/auth.json'
import enCollections from './locales/en/collections.json'
import enDashboard from './locales/en/dashboard.json'
import enErrors from './locales/en/errors.json'
import enExplorer from './locales/en/explorer.json'
import enFilters from './locales/en/filters.json'
import enInspector from './locales/en/inspector.json'
import enJobs from './locales/en/jobs.json'
import enLocale from './locales/en/locale.json'
import enNav from './locales/en/nav.json'
import enPlaceholder from './locales/en/placeholder.json'
import enPreview from './locales/en/preview.json'
import enQuality from './locales/en/quality.json'
import enRollback from './locales/en/rollback.json'
import enRules from './locales/en/rules.json'
import enSchedules from './locales/en/schedules.json'
import enSafety from './locales/en/safety.json'
import enSchemas from './locales/en/schemas.json'
import enStatus from './locales/en/status.json'
import enSystem from './locales/en/system.json'
import enTheme from './locales/en/theme.json'
import enTransformations from './locales/en/transformations.json'
import frApp from './locales/fr/app.json'
import frAnalytics from './locales/fr/analytics.json'
import frAuth from './locales/fr/auth.json'
import frCollections from './locales/fr/collections.json'
import frDashboard from './locales/fr/dashboard.json'
import frErrors from './locales/fr/errors.json'
import frExplorer from './locales/fr/explorer.json'
import frFilters from './locales/fr/filters.json'
import frInspector from './locales/fr/inspector.json'
import frJobs from './locales/fr/jobs.json'
import frLocale from './locales/fr/locale.json'
import frNav from './locales/fr/nav.json'
import frPlaceholder from './locales/fr/placeholder.json'
import frPreview from './locales/fr/preview.json'
import frQuality from './locales/fr/quality.json'
import frRollback from './locales/fr/rollback.json'
import frRules from './locales/fr/rules.json'
import frSchedules from './locales/fr/schedules.json'
import frSafety from './locales/fr/safety.json'
import frSchemas from './locales/fr/schemas.json'
import frStatus from './locales/fr/status.json'
import frSystem from './locales/fr/system.json'
import frTheme from './locales/fr/theme.json'
import frTransformations from './locales/fr/transformations.json'

export const rawCatalogs = {
  en: {
    schedules: enSchedules,
    auth: enAuth, analytics: enAnalytics, collections: enCollections, quality: enQuality, schemas: enSchemas,
    transformations: enTransformations, preview: enPreview, rollback: enRollback, rules: enRules,
    jobs: enJobs, inspector: enInspector, app: enApp, nav: enNav, status: enStatus,
    system: enSystem, dashboard: enDashboard, placeholder: enPlaceholder, filters: enFilters,
    explorer: enExplorer, safety: enSafety, locale: enLocale, theme: enTheme, errors: enErrors,
  },
  fr: {
    schedules: frSchedules,
    auth: frAuth, analytics: frAnalytics, collections: frCollections, quality: frQuality, schemas: frSchemas,
    transformations: frTransformations, preview: frPreview, rollback: frRollback, rules: frRules,
    jobs: frJobs, inspector: frInspector, app: frApp, nav: frNav, status: frStatus,
    system: frSystem, dashboard: frDashboard, placeholder: frPlaceholder, filters: frFilters,
    explorer: frExplorer, safety: frSafety, locale: frLocale, theme: frTheme, errors: frErrors,
  },
} as const

export type Locale = keyof typeof rawCatalogs
export type RawCatalog = (typeof rawCatalogs)[Locale]
