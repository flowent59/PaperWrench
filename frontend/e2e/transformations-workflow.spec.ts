import { expect, test, type Page } from '@playwright/test'

const labels = {
  en: { continue: 'Continue', back: 'Back', ids: 'Document IDs (comma separated)',
    review: 'Review your setup', value: 'Value', preview: 'Preview selection',
    acknowledge: 'I have reviewed the proposed changes', apply: 'Confirm and apply',
    before: 'Before', intended: 'Intended' },
  fr: { continue: 'Continuer', back: 'Retour', ids: 'ID de documents (séparés par des virgules)',
    review: 'Vérifier la configuration', value: 'Valeur', preview: 'Prévisualiser la sélection',
    acknowledge: "J'ai vérifié les changements proposés", apply: 'Confirmer et appliquer',
    before: 'Avant', intended: 'Prévu' },
} as const

async function mockWorkflow(page: Page, locale: 'en' | 'fr') {
  let previewAttempts = 0
  let jobCreates = 0
  let plannedTitle = ''
  await page.route('**/api/v1/**', async route => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (path === '/api/v1/auth/me') return route.fulfill({ json: {
      user_id: 53, username: 'tester', display_name: 'Tester',
      expires_at: '2099-01-01T00:00:00Z', csrf_token: 'workflow-csrf', locale,
    } })
    if (path === '/api/v1/system/health') return route.fulfill({ json: { status: 'ok' } })
    if (path === '/api/v1/system/paperless') return route.fulfill({ json: {
      configured: true, connected: true, compatible: true,
    } })
    if (path === '/api/v1/transformations/validate') return route.fulfill({ json: { valid: true, issues: [] } })
    if (path === '/api/v1/previews' && request.method() === 'POST') {
      previewAttempts += 1
      plannedTitle = request.postDataJSON().operations[0].value
      if (previewAttempts === 1) return route.fulfill({ status: 422, json: {
        error: { code: 'VALIDATION_ERROR', message: 'Try the dry run again' },
      } })
      return route.fulfill({ status: 201, json: {
        id: 'preview-53', version: 1, preview_token: 'opaque',
        created_at: new Date().toISOString(), expires_at: new Date(Date.now() + 600000).toISOString(),
        matched: 1, evaluated: 1, changed: 1, unchanged: 0, errors: 0, confirmed: false,
        selection_fingerprint: 'selection', spec_fingerprint: 'spec',
        target_fingerprint: 'targets', result_fingerprint: 'results',
      } })
    }
    if (path === '/api/v1/previews/preview-53/documents') return route.fulfill({ json: {
      items: [{ document_id: 12, title: 'Invoice', status: 'change', issue: null,
        changes: [{ field: { source: 'core', name: 'title' }, operation: 'set', status: 'change',
          before: { kind: 'present', raw: 'Old title' },
          intended: { kind: 'present', raw: plannedTitle }, issue: null }] }],
      page: 1, page_size: 25, total: 1, page_count: 1,
    } })
    if (path === '/api/v1/jobs' && request.method() === 'POST') {
      jobCreates += 1
      return route.fulfill({ status: 201, json: { id: 53 } })
    }
    if (path.startsWith('/api/v1/metadata/') || path === '/api/v1/filters/capabilities') {
      return route.fulfill({ json: [] })
    }
    return route.fulfill({ status: 404, json: { error: { code: 'NOT_FOUND', message: 'Not found' } } })
  })
  return { previewAttempts: () => previewAttempts, jobCreates: () => jobCreates }
}

for (const locale of ['en', 'fr'] as const) {
  for (const [layout, width] of [['desktop', 1440], ['tablet', 820], ['mobile', 390]] as const) {
    test(`guided transformations in ${locale.toUpperCase()} on ${layout}`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 })
      const api = await mockWorkflow(page, locale)
      const l = labels[locale]
      await page.goto('/transformations')
      await expect(page.locator('html')).toHaveAttribute('lang', locale)
      await page.getByLabel(l.ids).fill('12')
      await page.getByRole('button', { name: l.continue }).click()
      await page.getByLabel(l.value).fill('New title')
      await page.getByRole('button', { name: l.continue }).click()
      await expect(page.getByRole('heading', { name: l.review })).toBeVisible()
      await expect(page.getByText(/1 (selected document IDs|identifiants de documents sélectionnés)/)).toBeVisible()
      await page.getByRole('button', { name: l.back }).click()
      await expect(page.getByLabel(l.value)).toHaveValue('New title')
      await page.getByRole('button', { name: l.continue }).click()
      await page.getByRole('button', { name: l.continue }).click()
      expect(api.jobCreates()).toBe(0)
      await page.getByRole('button', { name: l.preview }).click()
      await expect(page.getByRole('alert')).toBeVisible()
      expect(api.jobCreates()).toBe(0)
      await page.getByRole('button', { name: l.preview }).click()
      await expect(page.getByText('Old title')).toBeVisible()
      await expect(page.getByText('New title')).toBeVisible()
      await expect(page.getByText(l.before, { exact: true })).toBeVisible()
      await expect(page.getByText(l.intended, { exact: true })).toBeVisible()
      await page.getByRole('button', { name: l.back }).click()
      await page.getByRole('button', { name: l.back }).click()
      await page.getByLabel(l.value).fill('Final title')
      await page.getByRole('button', { name: l.continue }).click()
      await page.getByRole('button', { name: l.continue }).click()
      await expect(page.getByRole('status').filter({ hasText: locale === 'fr'
        ? 'Relancez la simulation' : 'Run a new dry run' })).toBeVisible()
      expect(api.jobCreates()).toBe(0)
      await page.getByRole('button', { name: l.preview }).click()
      await expect(page.getByText('Final title')).toBeVisible()
      await expect(page.getByRole('button', { name: l.apply })).toBeDisabled()
      await page.getByRole('checkbox', { name: new RegExp(l.acknowledge) }).check()
      await page.getByRole('button', { name: l.apply }).click()
      await expect(page.locator('a[href="/jobs/53"]')).toBeVisible()
      expect(api.previewAttempts()).toBe(3)
      expect(api.jobCreates()).toBe(1)
    })
  }
}
