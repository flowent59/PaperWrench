import { expect, test } from '@playwright/test'

test('compiled SPA keeps nested routes after direct load and reload', async ({ page }) => {
  const pageErrors: string[] = []
  const failedAssets: string[] = []
  page.on('pageerror', error => pageErrors.push(error.message))
  page.on('response', response => {
    if (new URL(response.url()).pathname.startsWith('/assets/') && !response.ok()) {
      failedAssets.push(`${response.status()} ${response.url()}`)
    }
  })
  await page.route('**/api/v1/**', route => {
    if (new URL(route.request().url()).pathname === '/api/v1/auth/me') {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          user_id: 1, username: 'route-smoke', display_name: 'Route Smoke',
          expires_at: '2099-01-01T00:00:00Z', csrf_token: 'route-smoke-csrf', locale: 'en',
        }),
      })
    }
    return route.fulfill({
      status: 503,
      contentType: 'application/json',
      body: JSON.stringify({ error: { code: 'UNAVAILABLE', message: 'Route smoke test' } }),
    })
  })

  for (const [path, heading] of [
    ['/documents/42', 'Inspector'],
    ['/jobs/42', 'Job #42'],
    ['/collections/7', 'Collections'],
    ['/unknown/deep-route', '404'],
  ]) {
    const response = await page.goto(path)
    expect(response?.status()).toBe(200)
    await expect(page.getByRole('heading', { name: heading, exact: true })).toBeVisible()
    await page.reload()
    await expect(page.getByRole('heading', { name: heading, exact: true })).toBeVisible()
    expect(new URL(page.url()).pathname).toBe(path)
  }

  await page.getByRole('navigation', { name: 'Dashboard' }).getByRole('link', { name: 'Dashboard' }).click()
  await expect(page).toHaveURL('/')
  expect(pageErrors).toEqual([])
  expect(failedAssets).toEqual([])
})
