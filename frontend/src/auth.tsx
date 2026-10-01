import { useQueryClient } from '@tanstack/react-query'
import { Fragment, useEffect, useMemo, useState, type FormEvent, type ReactNode } from 'react'

import { authApi } from '@/api/client'
import type { AuthSession } from '@/api/types'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { getLocale, messages, setLocale as activateLocale, type Locale } from '@/i18n/messages'
import { errorMessage } from '@/i18n/errors'
import { AuthContext, type AuthContextValue } from '@/auth-context'

function LoginPage({ locale, onLocaleChange, onLogin }: {
  locale: Locale
  onLocaleChange: (locale: Locale) => void
  onLogin: (session: AuthSession) => void
}) {
  const [token, setToken] = useState('')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [remember, setRemember] = useState(true)
  const [available, setAvailable] = useState(false)
  const [optionsLoaded, setOptionsLoaded] = useState(false)
  const [mode, setMode] = useState<'token' | 'password'>('token')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    let active = true
    void authApi.options().then((options) => {
      if (active) setAvailable(options.remember_available)
    }).catch(() => {}).finally(() => { if (active) setOptionsLoaded(true) })
    return () => { active = false }
  }, [])

  async function submit(event: FormEvent) {
    event.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      onLogin(mode === 'password'
        ? await authApi.passwordLogin(username, password)
        : await authApi.login(token, locale, {
          remember: available && remember,
          ...(available && remember ? { password } : {}),
        }))
      setToken('')
      setPassword('')
    } catch (cause) {
      setError(errorMessage(cause, messages.auth.unreachable))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-background p-6">
      <Card className="w-full max-w-md">
        <CardHeader>
          <label className="ml-auto flex items-center gap-2 text-xs text-muted-foreground">
            <span>{messages.locale.language}</span>
            <select aria-label={messages.locale.language} className="rounded border border-input bg-background px-2 py-1"
              onChange={(event) => onLocaleChange(event.target.value as Locale)} value={locale}>
              <option value="en">{messages.locale.english}</option>
              <option value="fr">{messages.locale.french}</option>
            </select>
          </label>
          <CardTitle>{messages.auth.title}</CardTitle>
          <CardDescription>{messages.auth.description}</CardDescription>
        </CardHeader>
        <CardContent>
          {available && <div className="mb-4 flex gap-2">
            <Button type="button" variant={mode === 'token' ? 'default' : 'outline'} disabled={submitting}
              onClick={() => { setMode('token'); setPassword(''); setError(null) }}>
              {messages.auth.tokenMode}
            </Button>
            <Button type="button" variant={mode === 'password' ? 'default' : 'outline'} disabled={submitting}
              onClick={() => { setMode('password'); setToken(''); setPassword(''); setError(null) }}>
              {messages.auth.passwordMode}
            </Button>
          </div>}
          <form className="space-y-4" onSubmit={submit}>
            {mode === 'token' ? <label className="block space-y-2 text-sm font-medium">
              <span>{messages.auth.token}</span>
              <input
                autoComplete="off"
                autoFocus
                className="w-full rounded-md border border-input bg-background px-3 py-2 font-mono text-sm"
                name="paperless-token"
                onChange={(event) => setToken(event.target.value)}
                required
                maxLength={4096}
                type="password"
                value={token}
              />
            </label> : <label className="block space-y-2 text-sm font-medium">
              <span>{messages.auth.username}</span>
              <input autoComplete="username" className="w-full rounded-md border border-input bg-background px-3 py-2"
                required maxLength={255} value={username} onChange={(event) => setUsername(event.target.value)} />
            </label>}
            {mode === 'token' && available && <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={remember} onChange={(event) => {
                setRemember(event.target.checked); setPassword('')
              }} />
              {messages.auth.remember}
            </label>}
            {(mode === 'password' || (available && remember)) && <label className="block space-y-2 text-sm font-medium">
              <span>{messages.auth.password}</span>
              <input type="password" autoComplete={mode === 'password' ? 'current-password' : 'new-password'}
                className="w-full rounded-md border border-input bg-background px-3 py-2"
                required minLength={mode === 'password' ? 1 : 12} maxLength={1024} value={password}
                onChange={(event) => setPassword(event.target.value)} />
            </label>}
            <p className="text-xs text-muted-foreground">{mode === 'password' ? messages.auth.recovery
              : available && remember ? messages.auth.rememberHelp : messages.auth.storage}</p>
            {error && <p className="text-sm text-destructive" role="alert">{error}</p>}
            <Button className="w-full" disabled={submitting || !optionsLoaded
              || (mode === 'token' ? !token.trim() : !username || !password)} type="submit">
              {submitting ? messages.auth.signingIn : messages.auth.signIn}
            </Button>
          </form>
        </CardContent>
      </Card>
    </main>
  )
}

export function AuthGate({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<AuthSession | null | undefined>(undefined)
  const [locale, setLocaleState] = useState<Locale>(getLocale)
  const queryClient = useQueryClient()

  function applyLocale(next: Locale) {
    activateLocale(next)
    setLocaleState(next)
  }

  useEffect(() => {
    let active = true
    void authApi.me().then((value) => {
      if (!active) return
      const preferred = value.locale ?? getLocale()
      activateLocale(preferred)
      setLocaleState(preferred)
      setSession({ ...value, locale: preferred })
    }).catch(() => active && setSession(null))
    const unauthorized = () => {
      queryClient.clear()
      setSession(null)
    }
    const updated = (event: Event) => setSession((event as CustomEvent<AuthSession>).detail)
    window.addEventListener('paperwrench:session-updated', updated)
    window.addEventListener('paperwrench:unauthorized', unauthorized)
    return () => {
      active = false
      window.removeEventListener('paperwrench:unauthorized', unauthorized)
      window.removeEventListener('paperwrench:session-updated', updated)
    }
  }, [queryClient])

  const value = useMemo<AuthContextValue | null>(() => session ? ({
    session,
    locale,
    changeLocale: async (next) => {
      const previous = locale
      applyLocale(next)
      setSession((current) => current ? { ...current, locale: next } : current)
      try {
        await authApi.updateLocale(next)
      } catch {
        applyLocale(previous)
        setSession((current) => current ? { ...current, locale: previous } : current)
      }
    },
    logout: async () => {
      try { await authApi.logout() } finally {
        queryClient.clear()
        setSession(null)
      }
    },
  }) : null, [locale, queryClient, session])

  if (session === undefined) {
    return <main className="flex min-h-screen items-center justify-center">{messages.auth.checking}</main>
  }
  if (!value) {
    return <LoginPage locale={locale} onLocaleChange={applyLocale} onLogin={(next) => {
      const preferred = next.locale ?? locale
      applyLocale(preferred)
      setSession({ ...next, locale: preferred })
    }} />
  }
  return <AuthContext.Provider value={value}>
    <Fragment key={locale}>{children}</Fragment>
  </AuthContext.Provider>
}
