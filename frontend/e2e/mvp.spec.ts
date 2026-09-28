import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '@playwright/test'

test('compiled MVP: authenticated Explorer → preview → Job → rollback → logout', async ({ page, playwright }) => {
  expect(process.env.PAPERWRENCH_ALLOW_LIVE_TESTS).toBe('true')
  const token = process.env.PAPERWRENCH_E2E_TOKEN ?? ''
  expect(token.length).toBeGreaterThan(10)
  const target = new URL(process.env.PAPERLESS_URL ?? '')
  expect(['127.0.0.1', 'localhost']).toContain(target.hostname)
  expect(target.port).toBe('8010')
  const upstream = await playwright.request.newContext({ baseURL: target.origin,
    extraHTTPHeaders: { Authorization: `Token ${token}`, Accept: 'application/json; version=10' } })
  const probe = await upstream.get('/api/documents/?page_size=1')
  expect(probe.status()).toBe(200)
  expect(probe.headers()['x-version']).toBe(process.env.PAPERWRENCH_EXPECTED_PAPERLESS_VERSION ?? '3.2.1')
  expect((await probe.json()).count).toBeLessThanOrEqual(500)
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Sign in to PaperWrench' })).toBeVisible()
  await page.getByLabel('Language').selectOption('fr')
  await expect(page.getByRole('heading', { name: 'Se connecter à PaperWrench' })).toBeVisible()
  await expect(page.locator('html')).toHaveAttribute('lang', 'fr')
  await page.getByLabel('Langue').selectOption('en')
  await expect(page.getByRole('heading', { name: 'Sign in to PaperWrench' })).toBeVisible()
  await page.getByLabel('Paperless API token').fill(token)
  const loginResponse = page.waitForResponse(response =>
    response.url().endsWith('/api/v1/auth/login') && response.request().method() === 'POST')
  await page.getByRole('button', { name: 'Sign in', exact: true }).click()
  expect((await loginResponse).status()).toBe(200)
  await expect(page.getByRole('navigation', { name: 'Dashboard' })).toBeVisible()
  const sessionCookie = (await page.context().cookies()).find(
    cookie => cookie.name === 'paperwrench_session',
  )
  expect(sessionCookie).toMatchObject({ httpOnly: true, sameSite: 'Strict' })
  expect(sessionCookie?.value).not.toContain(token)
  const browserStorage = await page.evaluate<string>(
    'JSON.stringify({local: {...localStorage}, session: {...sessionStorage}})',
  )
  expect(browserStorage).not.toContain(token)
  const app = page.request
  const authenticated = await (await app.get('/api/v1/auth/me')).json()
  expect(authenticated.username).toBeTruthy()
  expect(JSON.stringify(authenticated)).not.toContain(token)
  const writeHeaders = { 'X-CSRF-Token': authenticated.csrf_token }
  const types = await (await app.get('/api/v1/metadata/document-types')).json()
  const fields = await (await app.get('/api/v1/metadata/custom-fields')).json()
  const vacation = types.find((item: { name: string }) => item.name === 'Relevé de vacations')
  const period = fields.find((item: { name: string }) => item.name === 'Période concernée')
  const amount = fields.find((item: { name: string }) => item.name === 'Montant')
  expect(vacation).toBeTruthy()
  expect(period).toBeTruthy()
  expect(amount).toBeTruthy()
  const condition = { kind: 'condition', field: { source: 'core', name: 'document_type' }, operator: 'equals', value: vacation.id }
  const scope = { filters: { root: { kind: 'group', operator: 'and', children: [condition] } } }
  const originalPage = await upstream.get(`/api/documents/?document_type__id=${vacation.id}&page_size=100`)
  type Doc = { id: number; title: string; custom_fields: { field: number; value: unknown }[] }
  const originals: Doc[] = (await originalPage.json()).results
  expect(originals.length).toBeGreaterThan(2)
  const schemaResponse = await app.post('/api/v1/schemas', { headers: writeHeaders, data: {
    name: 'M13 vacations', applies_when: scope,
    rules: [{ kind: 'required', field: { source: 'custom_field', field_id: amount.id }, field_type: 'monetary' }],
  } })
  expect(schemaResponse.ok()).toBeTruthy()
  const schema = await schemaResponse.json()
  await page.goto(`/documents/${originals[0].id}`)
  await expect(page.getByRole('heading', { name: 'Inspector', exact: true })).toBeVisible()
  await page.getByRole('link', { name: 'Collections', exact: true }).click()
  await page.getByLabel('Name').fill('M13 browser collection')
  await page.getByRole('button', { name: 'Create collection' }).click()
  await expect(page.getByRole('link', { name: 'M13 browser collection' })).toBeVisible()
  const collections = await (await app.get('/api/v1/collections')).json()
  const collection = collections.find((item: { name: string }) => item.name === 'M13 browser collection')
  expect(collection).toBeTruthy()
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  async function accessible() {
    const result = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()
    expect(result.violations.map(item => ({ id: item.id, nodes: item.nodes.map(node => node.target) }))).toEqual([])
  }
  const explorer = (query: unknown) => `/documents?quality_query=${encodeURIComponent(JSON.stringify(query))}`
  try {
    await page.goto(explorer(scope))
    await expect(page.getByRole('columnheader', { name: /Période concernée/ })).toBeVisible()
    await expect(page.getByRole('columnheader', { name: /Montant/ })).toBeVisible()
    await accessible()
    await page.getByRole('link', { name: 'Schemas', exact: true }).click()
    const schemaButton = page.getByRole('button', { name: 'M13 vacations' })
    await schemaButton.click()
    await expect(schemaButton).toHaveClass(/bg-primary/)
    await expect(schemaButton).toHaveCSS('transition-property', 'box-shadow')
    await accessible()
    await page.getByRole('button', { name: 'Evaluate', exact: true }).click()
    await expect(page.getByText(/matching documents/)).toBeVisible()
    await accessible()
    await page.getByRole('link', { name: 'Data Quality', exact: true }).click()
    await expect(page.getByRole('link', { name: 'Open exact violation condition in Explorer' })).toBeVisible()
    const quality = await (await app.get(`/api/v1/quality/schemas/${schema.id}`)).json()
    const zeros = originals.filter(doc => doc.custom_fields.some(field => field.field === amount.id && field.value === 'EUR0.00'))
    expect(zeros.length).toBeGreaterThan(0)
    expect(quality.violation_count).toBeGreaterThan(0)
    expect(quality.items.every((item: { document_id: number }) => !zeros.some(doc => doc.id === item.document_id))).toBe(true)
    await accessible()
    await page.getByRole('link', { name: 'Open exact violation condition in Explorer' }).click()
    await expect(page.getByRole('heading', { name: 'Explorer', exact: true })).toBeVisible()
    const valid = { filters: { root: { kind: 'group', operator: 'and', children: [condition,
      { kind: 'condition', field: { source: 'custom_field', field_id: period.id }, operator: 'has_value' }] } } }
    await page.goto(explorer(valid))
    const transform = page.getByRole('link', { name: 'Transform all matching documents' })
    await expect(transform).toBeVisible()
    await transform.focus()
    await page.keyboard.press('Enter')
    await page.getByRole('combobox', { name: 'Operation 1', exact: true }).selectOption('template')
    await page.getByLabel('Template', { exact: true }).fill('Relevé de vacations – {period}')
    await page.getByRole('combobox', { name: '{period}', exact: true }).selectOption(`custom_field:${period.id}`)
    await accessible()
    await page.getByRole('button', { name: 'Preview selection' }).click()
    const confirm = page.getByRole('button', { name: 'Confirm and apply' })
    await expect(confirm).toBeDisabled()
    await page.getByRole('checkbox', { name: /I have reviewed the proposed changes/ }).focus()
    await page.keyboard.press('Space')
    await expect(confirm).toBeEnabled()
    await accessible()
    await page.keyboard.press('Tab')
    await expect(confirm).toBeFocused()
    await page.keyboard.press('Enter')
    await page.getByRole('link', { name: 'Open History' }).click()
    await expect(page.getByText(/^COMPLETED ·/)).toBeVisible({ timeout: 30_000 })
    const jobId = Number(new URL(page.url()).pathname.split('/').pop())
    // Direct nested navigation/reload must load actual compiled assets.
    await page.reload()
    await expect(page.getByRole('heading', { name: `Job #${jobId}`, exact: true })).toBeVisible()
    await page.getByRole('button', { name: 'Inspect operations' }).first().click()
    await expect(page.getByText('Confirmed write; eligible for rollback review.')).toBeVisible()
    await accessible()
    const targets = await (await app.get(`/api/v1/jobs/${jobId}/targets?page_size=100`)).json()
    const edited = targets.items.find((item: { status: string }) => item.status === 'succeeded').document_id
    expect((await upstream.patch(`/api/documents/${edited}/`, { data: { title: 'M13 later third-party title' } })).ok()).toBeTruthy()
    await page.getByRole('link', { name: 'History', exact: true }).first().click()
    await page.locator(`a[href="/jobs/${jobId}"]`).click()
    await page.getByRole('button', { name: 'Preview rollback' }).click()
    await expect(page.getByText(/ROLLBACK_CONFLICT/)).toBeVisible()
    await page.getByRole('checkbox', { name: 'I have reviewed the restorations, exclusions and conflicts.' }).check()
    await page.getByRole('button', { name: 'Create rollback Job' }).click()
    await page.getByRole('link', { name: /Rollback Job #/ }).click()
    await expect(page.getByText(/^PARTIAL ·/)).toBeVisible({ timeout: 30_000 })
    for (const before of originals) {
      const current: Doc = await (await upstream.get(`/api/documents/${before.id}/`)).json()
      expect(current.title).toBe(before.id === edited ? 'M13 later third-party title' : before.title)
      expect(current.custom_fields).toEqual(before.custom_fields)
    }
    // A transient disconnected browser renders an error and recovers on reload.
    await page.route('**/api/v1/jobs*', route => route.abort())
    await page.goto('/history')
    await expect(page.getByRole('alert')).toBeVisible()
    await page.unroute('**/api/v1/jobs*')
    await page.reload()
    await expect(page.locator(`a[href="/jobs/${jobId}"]`)).toBeVisible()
    await accessible()
    expect(errors).toEqual([])
  } finally {
    for (const doc of originals) {
      expect((await upstream.patch(`/api/documents/${doc.id}/`, { data: { title: doc.title } })).ok()).toBeTruthy()
    }
    await app.delete(`/api/v1/schemas/${schema.id}`, { headers: writeHeaders })
    await app.delete(`/api/v1/collections/${collection.id}`, { headers: writeHeaders })
    await upstream.dispose()
  }
  expect(await page.evaluate<string>(
    'JSON.stringify({local: {...localStorage}, session: {...sessionStorage}})',
  )).not.toContain(token)
  await page.getByLabel('Language').selectOption('fr')
  await expect(page.getByRole('navigation', { name: 'Tableau de bord' })).toBeVisible()
  await expect.poll(async () => (await (await app.get('/api/v1/auth/me')).json()).locale).toBe('fr')
  await page.getByRole('button', { name: 'Se déconnecter' }).click()
  await expect(page.getByRole('heading', { name: 'Se connecter à PaperWrench' })).toBeVisible()
  expect((await app.get('/api/v1/auth/me')).status()).toBe(401)

  // The pre-login choice is only a browser fallback. The authenticated
  // Paperless user's persisted preference wins on the next session.
  await page.getByLabel('Langue').selectOption('en')
  await page.getByLabel('Paperless API token').fill(token)
  await page.getByRole('button', { name: 'Sign in', exact: true }).click()
  await expect(page.getByRole('navigation', { name: 'Tableau de bord' })).toBeVisible()
  expect((await (await app.get('/api/v1/auth/me')).json()).locale).toBe('fr')
  await page.getByRole('button', { name: 'Se déconnecter' }).click()
  await expect(page.getByRole('heading', { name: 'Se connecter à PaperWrench' })).toBeVisible()

  // A stale/expired opaque cookie must also return the browser to login; the
  // actual server-side expiry and credential destruction are unit-tested.
  await page.context().addCookies([{
    name: 'paperwrench_session', value: 'expired-session', url: 'http://127.0.0.1:8020',
  }])
  await page.reload()
  await expect(page.getByRole('heading', { name: 'Se connecter à PaperWrench' })).toBeVisible()
})
