import { expect, test } from '@playwright/test'

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
