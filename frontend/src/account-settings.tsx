import { useEffect, useState, type FormEvent } from 'react'

import { authApi } from '@/api/client'
import { useAuth } from '@/auth-context'
import { Button } from '@/components/ui/button'
import { errorMessage } from '@/i18n/errors'
import { messages } from '@/i18n/messages'

export function AccountSettings() {
  const { session } = useAuth()
  const [available, setAvailable] = useState(false)
  const [remembered, setRemembered] = useState(session.remembered ?? false)
  const [token, setToken] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    let active = true
    void authApi.options().then((options) => {
      if (active) setAvailable(options.remember_available)
    }).catch(() => {})
    return () => { active = false }
  }, [])

  async function save(event: FormEvent) {
    event.preventDefault()
    setBusy(true); setError(null); setSaved(false)
    try {
      const next = await authApi.replaceCredentials(token, password)
      window.dispatchEvent(new CustomEvent('paperwrench:session-updated', { detail: next }))
      setRemembered(true); setToken(''); setPassword(''); setSaved(true)
    } catch (cause) { setError(errorMessage(cause)) }
    finally { setBusy(false) }
  }

  async function remove() {
    setBusy(true); setError(null); setSaved(false)
    try { await authApi.deleteCredentials() }
    catch (cause) { setError(errorMessage(cause)) }
    finally { setBusy(false) }
  }

  return <details className="relative shrink-0" onToggle={(event) => {
    if (!event.currentTarget.open) { setToken(''); setPassword(''); setError(null); setSaved(false) }
  }}>
    <summary className="cursor-pointer whitespace-nowrap text-sm">{messages.auth.account}</summary>
    <div className="absolute right-0 top-8 z-50 w-80 space-y-3 rounded border bg-background p-4 shadow-lg">
      <p className="break-words text-sm">{messages.auth.username}: {session.username}</p>
      <p className="text-xs text-muted-foreground">{remembered ? messages.auth.isRemembered : messages.auth.storage}</p>
      {available && <form className="space-y-3" onSubmit={save}>
        <p className="text-xs text-muted-foreground">{messages.auth.replaceHelp}</p>
        <label className="block text-sm">{messages.auth.token}
          <input className="mt-1 w-full rounded border bg-background p-2" type="password"
            autoComplete="off" required maxLength={4096} value={token} onChange={(event) => setToken(event.target.value)} />
        </label>
        <label className="block text-sm">{messages.auth.password}
          <input className="mt-1 w-full rounded border bg-background p-2" type="password"
            autoComplete="new-password" required minLength={12} maxLength={1024} value={password}
            onChange={(event) => setPassword(event.target.value)} />
        </label>
        <Button type="submit" disabled={busy}>{messages.auth.saveCredentials}</Button>
      </form>}
      {remembered && <>
        <p className="text-xs text-muted-foreground">{messages.auth.deleteHelp}</p>
        <Button type="button" variant="outline" disabled={busy} onClick={() => void remove()}>
          {messages.auth.deleteCredentials}
        </Button>
      </>}
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      {saved && <p role="status" className="text-sm">{messages.auth.saved}</p>}
    </div>
  </details>
}
