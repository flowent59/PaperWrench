import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { AccountSettings } from '@/account-settings'
import { ApiError, authApi } from '@/api/client'
import type { AuthSession } from '@/api/types'
import { AuthGate } from '@/auth'
import { useAuth } from '@/auth-context'
import { messages, setLocale } from '@/i18n/messages'

const session: AuthSession = {
  user_id: 42, username: 'alice', display_name: 'Alice', locale: 'en',
  csrf_token: 'csrf-test', expires_at: '2030-01-01T00:00:00Z', remembered: true,
}

function Content() {
  const { session: current } = useAuth()
  return <><p>{current.username}</p><AccountSettings /></>
}

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><AuthGate><Content /></AuthGate></QueryClientProvider>)
}

beforeEach(() => {
  setLocale('en')
  vi.spyOn(authApi, 'me').mockRejectedValue(new Error('No session'))
  vi.spyOn(authApi, 'options').mockResolvedValue({ remember_available: true })
  vi.spyOn(authApi, 'login').mockResolvedValue(session)
  vi.spyOn(authApi, 'passwordLogin').mockResolvedValue(session)
  vi.spyOn(authApi, 'replaceCredentials').mockResolvedValue(session)
  vi.spyOn(authApi, 'deleteCredentials').mockResolvedValue(undefined)
})

afterEach(() => { cleanup(); vi.restoreAllMocks(); setLocale('en') })

describe('local accounts', () => {
  it.each(['en', 'fr'] as const)('offers enrollment by default without asking for a username (%s)', async (locale) => {
    setLocale(locale)
    const user = userEvent.setup()
    mount()
    expect(await screen.findByLabelText(messages.auth.remember)).toBeChecked()
    expect(screen.queryByLabelText(messages.auth.username)).not.toBeInTheDocument()
    await user.type(screen.getByLabelText(messages.auth.token), 'private-paperless-token')
    await user.type(screen.getByLabelText(messages.auth.password), 'local-long-password')
    await user.click(screen.getByRole('button', { name: messages.auth.signIn }))
    await waitFor(() => expect(authApi.login).toHaveBeenCalledWith('private-paperless-token', locale, {
      remember: true, password: 'local-long-password',
    }))
    expect(await screen.findByText('alice')).toBeInTheDocument()
  })

  it('allows opting out and omits the password', async () => {
    const user = userEvent.setup()
    mount()
    await user.click(await screen.findByLabelText(messages.auth.remember))
    expect(screen.queryByLabelText(messages.auth.password)).not.toBeInTheDocument()
    await user.type(screen.getByLabelText(messages.auth.token), 'ephemeral-token')
    await user.click(screen.getByRole('button', { name: messages.auth.signIn }))
    await waitFor(() => expect(authApi.login).toHaveBeenCalledWith('ephemeral-token', 'en', { remember: false }))
  })

  it('uses username and local password on reconnect and shows recovery on failure', async () => {
    vi.mocked(authApi.passwordLogin).mockRejectedValue(new ApiError(401, { code: 'AUTH_INVALID_CREDENTIALS' }))
    const user = userEvent.setup()
    mount()
    await user.click(await screen.findByRole('button', { name: messages.auth.passwordMode }))
    expect(screen.queryByLabelText(messages.auth.token)).not.toBeInTheDocument()
    await user.type(screen.getByLabelText(messages.auth.username), 'alice')
    await user.type(screen.getByLabelText(messages.auth.password), 'local-long-password')
    await user.click(screen.getByRole('button', { name: messages.auth.signIn }))
    expect(await screen.findByRole('alert')).toHaveTextContent(messages.errors.codes.AUTH_INVALID_CREDENTIALS)
    expect(authApi.passwordLogin).toHaveBeenCalledWith('alice', 'local-long-password')
    expect(screen.getByText(messages.auth.recovery)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: messages.auth.tokenMode }))
    expect(screen.getByLabelText(messages.auth.password)).toHaveValue('')
  })

  it('keeps token-only login when the operator disables memorization', async () => {
    vi.mocked(authApi.options).mockResolvedValue({ remember_available: false })
    const user = userEvent.setup()
    mount()
    await user.type(await screen.findByLabelText(messages.auth.token), 'token-only')
    expect(screen.queryByLabelText(messages.auth.remember)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: messages.auth.passwordMode })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: messages.auth.signIn }))
    await waitFor(() => expect(authApi.login).toHaveBeenCalledWith('token-only', 'en', { remember: false }))
  })

  it('replaces saved credentials and clears the secret fields', async () => {
    vi.mocked(authApi.me).mockResolvedValue(session)
    const user = userEvent.setup()
    mount()
    await user.click(await screen.findByText(messages.auth.account))
    await user.type(await screen.findByLabelText(messages.auth.token), 'replacement-token')
    await user.type(screen.getByLabelText(messages.auth.password), 'replacement-password')
    await user.click(screen.getByRole('button', { name: messages.auth.saveCredentials }))
    expect(await screen.findByRole('status')).toHaveTextContent(messages.auth.saved)
    expect(authApi.replaceCredentials).toHaveBeenCalledWith('replacement-token', 'replacement-password')
    expect(screen.getByLabelText(messages.auth.token)).toHaveValue('')
    expect(screen.getByLabelText(messages.auth.password)).toHaveValue('')
  })

  it('can forget credentials even when new memorization is disabled', async () => {
    vi.mocked(authApi.me).mockResolvedValue(session)
    vi.mocked(authApi.options).mockResolvedValue({ remember_available: false })
    const user = userEvent.setup()
    mount()
    await user.click(await screen.findByText(messages.auth.account))
    expect(screen.queryByLabelText(messages.auth.token)).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: messages.auth.deleteCredentials }))
    expect(authApi.deleteCredentials).toHaveBeenCalledOnce()
  })
})
