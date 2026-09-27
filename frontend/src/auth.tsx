import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState, type FormEvent, type ReactNode } from 'react'

import { ApiError, authApi } from '@/api/client'
import type { AuthSession } from '@/api/types'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { messages } from '@/i18n/messages'
import { AuthContext, type AuthContextValue } from '@/auth-context'

function LoginPage({ onLogin }: { onLogin: (session: AuthSession) => void }) {
  const [token, setToken] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function submit(event: FormEvent) {
    event.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      onLogin(await authApi.login(token))
      setToken('')
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : messages.auth.unreachable)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-background p-6">
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle>{messages.auth.title}</CardTitle>
          <CardDescription>{messages.auth.description}</CardDescription>
        </CardHeader>
        <CardContent>
          <form className="space-y-4" onSubmit={submit}>
            <label className="block space-y-2 text-sm font-medium">
              <span>{messages.auth.token}</span>
              <input
                autoComplete="off"
                autoFocus
                className="w-full rounded-md border border-input bg-background px-3 py-2 font-mono text-sm"
                name="paperless-token"
                onChange={(event) => setToken(event.target.value)}
                required
                type="password"
                value={token}
              />
            </label>
            <p className="text-xs text-muted-foreground">{messages.auth.storage}</p>
            {error && <p className="text-sm text-destructive" role="alert">{error}</p>}
            <Button className="w-full" disabled={submitting || !token.trim()} type="submit">
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
  const queryClient = useQueryClient()

  useEffect(() => {
    let active = true
    void authApi.me().then((value) => active && setSession(value)).catch(() => active && setSession(null))
    const unauthorized = () => {
      queryClient.clear()
      setSession(null)
    }
    window.addEventListener('paperwrench:unauthorized', unauthorized)
    return () => {
      active = false
      window.removeEventListener('paperwrench:unauthorized', unauthorized)
    }
  }, [queryClient])

  const value = useMemo<AuthContextValue | null>(() => session ? ({
    session,
    logout: async () => {
      try { await authApi.logout() } finally {
        queryClient.clear()
        setSession(null)
      }
    },
  }) : null, [queryClient, session])

  if (session === undefined) {
    return <main className="flex min-h-screen items-center justify-center">{messages.auth.checking}</main>
  }
  if (!value) return <LoginPage onLogin={setSession} />
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
