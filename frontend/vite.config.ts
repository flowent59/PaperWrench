import path from 'node:path'

import react from '@vitejs/plugin-react'
// vitest/config re-exports Vite's defineConfig with the `test` block typed.
import { defineConfig } from 'vitest/config'

/**
 * The SPA is served by FastAPI from the same origin in production
 * (ADR-0001), so the build output goes straight into the backend's static
 * directory and every asset URL stays relative.
 */
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': path.resolve(__dirname, './src') },
  },
  base: './',
  build: {
    outDir: path.resolve(__dirname, '../backend/src/paperwrench/static'),
    emptyOutDir: true,
    sourcemap: true,
  },
  server: {
    port: 5173,
    // Split-origin only during development; production is single-origin.
    proxy: {
      '/api': {
        target: process.env.PAPERWRENCH_DEV_BACKEND ?? 'http://localhost:8000',
        changeOrigin: false,
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    css: false,
  },
})
