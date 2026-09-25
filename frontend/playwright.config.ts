import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  workers: 1,
  retries: 0,
  timeout: 180_000,
  expect: { timeout: 15_000 },
  reporter: 'list',
  use: {
    actionTimeout: 15_000,
    baseURL: 'http://127.0.0.1:8020',
    viewport: { width: 1440, height: 1000 },
    // No traces: Node-side requests carry a disposable sandbox credential.
    trace: 'off',
  },
})
