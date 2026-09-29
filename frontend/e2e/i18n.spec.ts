import { expect, test } from '@playwright/test'

const job = {
  id: 45, type: 'transform', rollback_of_job_id: null, rollback_job_id: null,
  title: 'Locale check', status: 'completed', total: 20000, processed: 12345,
  counts: { pending: 0, reading: 0, writing: 0, succeeded: 12345, unchanged: 7655,
    conflict: 0, permission: 0, missing: 0, failed: 0, ambiguous: 0 },
  source_kind: 'ids', dataset_query: null, operations: [], preview: null,
  created_at: '2024-01-15T10:00:00Z', started_at: '2024-01-15T10:00:01Z',
  finished_at: '2024-01-15T10:01:00Z', resumable: false,
}

async function mockAuthenticated(page: import('@playwright/test').Page, initialLocale: 'en' | 'fr') {
  let persistedLocale = initialLocale
  await page.route('**/api/v1/**', async route => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (path === '/api/v1/auth/me') return route.fulfill({ json: {
      user_id: 45, username: 'translator', display_name: 'Translator',
      expires_at: '2099-01-01T00:00:00Z', csrf_token: 'i18n-csrf', locale: persistedLocale,
    } })
    if (path === '/api/v1/auth/preferences' && request.method() === 'PATCH') {
      persistedLocale = request.postDataJSON().locale
      return route.fulfill({ json: { locale: persistedLocale } })
    }
    if (path === '/api/v1/jobs') return route.fulfill({ json: {
      items: [job], page: 1, page_size: 25, total: 1, page_count: 1,
    } })
    if (path === '/api/v1/system/health') return route.fulfill({ json: { status: 'ok' } })
    if (path === '/api/v1/system/paperless') return route.fulfill({ json: {
      configured: true, connected: true, compatible: true,
    } })
    return route.fulfill({ status: 404, json: { error: { code: 'NOT_FOUND', message: 'Not found' } } })
  })
  return () => persistedLocale
}

test.use({ locale: 'fr-FR' })

test('first visit detects French before authentication', async ({ page }) => {
  await page.route('**/api/v1/auth/me', route => route.fulfill({
    status: 401,
    contentType: 'application/json',
    body: JSON.stringify({ error: { code: 'AUTH_REQUIRED', message: 'Authentication required.' } }),
  }))

  await page.goto('/')
  await expect(page.locator('html')).toHaveAttribute('lang', 'fr')
  await expect(page.getByRole('heading', { name: 'Se connecter à PaperWrench' })).toBeVisible()
  await expect(page.getByLabel('Langue')).toHaveValue('fr')
})

for (const locale of ['en', 'fr'] as const) {
  test(`renders history and Intl values in ${locale.toUpperCase()}`, async ({ page }) => {
    await mockAuthenticated(page, locale)
    await page.goto('/history')
    await expect(page.locator('html')).toHaveAttribute('lang', locale)
    await expect(page.getByRole('heading', { name: locale === 'fr' ? 'Historique' : 'History' })).toBeVisible()

    const formatted = await page.evaluate(({ value, date, intlLocale }) => ({
      number: new Intl.NumberFormat(intlLocale).format(value),
      date: new Intl.DateTimeFormat(intlLocale, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(date)),
    }), { value: job.processed, date: job.created_at, intlLocale: locale === 'fr' ? 'fr-FR' : 'en-GB' })
    const row = page.getByRole('row').filter({ hasText: 'Locale check #45' })
    await expect(row).toContainText(`${formatted.number} /`)
    await expect(row).toContainText(formatted.date)
  })
}

test('persists an authenticated locale change across reloads', async ({ page }) => {
  const persistedLocale = await mockAuthenticated(page, 'en')
  await page.goto('/history')
  await page.getByLabel('Language').selectOption('fr')
  await expect(page.getByRole('heading', { name: 'Historique' })).toBeVisible()
  await expect.poll(persistedLocale).toBe('fr')
  await page.reload()
  await expect(page.locator('html')).toHaveAttribute('lang', 'fr')
  await expect(page.getByLabel('Langue')).toHaveValue('fr')
})
