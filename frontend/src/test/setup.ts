import '@testing-library/jest-dom/vitest'

import { vi } from 'vitest'

// jsdom does not implement matchMedia, which the theme provider reads.
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }),
})
