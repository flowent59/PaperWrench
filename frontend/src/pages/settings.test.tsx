import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'

import { authApi, systemApi } from '@/api/client'
import { AuthContext } from '@/auth-context'
import { ThemeProvider } from '@/components/theme-provider'
import { messages } from '@/i18n/messages'
import { SettingsPage } from './settings'

it('uses codes for safe diagnostics and reports failed language saves', async () => {
  const probe = vi.spyOn(systemApi, 'paperless').mockResolvedValue({
    configured: true, connected: false, compatible: false,
    url: 'https://private-host.invalid', api_version: null, paperless_version: null,
    requested_api_version: null, document_count: null,
    error_code: 'PAPERLESS_UNAUTHORIZED', error_message: 'private-server-response',
  })
  vi.spyOn(authApi, 'options').mockResolvedValue({ remember_available: false })
  const changeLocale = vi.fn().mockRejectedValue(new Error('private-save-error'))
  render(<QueryClientProvider client={new QueryClient()}>
    <AuthContext.Provider value={{
      locale: 'en', changeLocale, logout: vi.fn(),
      session: { user_id: 1, username: 'alice', display_name: 'Alice', locale: 'en', csrf_token: '',
        expires_at: '2030-01-01T00:00:00Z' },
    }}>
      <ThemeProvider><SettingsPage /></ThemeProvider>
    </AuthContext.Provider>
  </QueryClientProvider>)
  await screen.findByText(messages.system.checkCredentials)
  const user = userEvent.setup()
  await user.click(screen.getByRole('button', { name: messages.system.checkAgain }))
  expect(probe).toHaveBeenCalledTimes(2)
  await user.selectOptions(screen.getByLabelText(messages.locale.language), 'fr')
  expect(changeLocale).toHaveBeenCalledWith('fr')
  expect(await screen.findByRole('alert')).toHaveTextContent(messages.errors.generic)
  expect(screen.queryByText(/private-/)).not.toBeInTheDocument()
  vi.restoreAllMocks()
})
