import { useState } from 'react'

import { AccountSettings } from '@/account-settings'
import { usePaperlessStatus } from '@/api/queries'
import { useAuth } from '@/auth-context'
import { useTheme, type Theme } from '@/components/theme-provider'
import { Button } from '@/components/ui/button'
import { errorMessage } from '@/i18n/errors'
import { messages, type Locale } from '@/i18n/messages'

export function SettingsPage() {
  const { locale, changeLocale } = useAuth()
  const { theme, setTheme } = useTheme()
  const status = usePaperlessStatus()
  const [error, setError] = useState('')
  const [savingLocale, setSavingLocale] = useState(false)
  const m = messages.system
  const result = status.data
  const diagnosis = status.isFetching ? messages.status.checking
    : status.isError || !result ? m.retryBackend
    : !result.configured ? m.configurePaperless
    : ['PAPERLESS_UNAUTHORIZED', 'PAPERLESS_FORBIDDEN'].includes(result.error_code ?? '') ? m.checkCredentials
    : !result.connected ? m.checkPaperless
    : !result.compatible ? m.checkVersion
    : messages.status.connected

  async function updateLocale(next: Locale) {
    setError('')
    setSavingLocale(true)
    try {
      await changeLocale(next)
    } catch (cause) {
      setError(errorMessage(cause))
    } finally {
      setSavingLocale(false)
    }
  }

  return <main className="mx-auto max-w-4xl space-y-5 p-4 sm:p-6">
    <h1 className="text-2xl font-semibold">{messages.nav.settings}</h1>
    <div className="grid gap-4 md:grid-cols-2">
      <section className="space-y-3 rounded-lg border p-4">
        <h2 className="text-lg font-medium">{m.preferences}</h2>
        <label className="block space-y-1">
          <span className="block">{messages.locale.language}</span>
          <select className="rounded-md border bg-background p-2" value={locale} disabled={savingLocale}
            onChange={(event) => void updateLocale(event.target.value as Locale)}>
            <option value="en">{messages.locale.english}</option>
            <option value="fr">{messages.locale.french}</option>
          </select>
        </label>
        <p className="text-sm text-muted-foreground">{m.languageScope}</p>
        <label className="block space-y-1">
          <span className="block">{m.theme}</span>
          <select className="rounded-md border bg-background p-2" value={theme}
            onChange={(event) => setTheme(event.target.value as Theme)}>
            <option value="light">{m.light}</option>
            <option value="dark">{m.dark}</option>
          </select>
        </label>
        <p className="text-sm text-muted-foreground">{m.themeScope}</p>
        {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      </section>
      <section className="space-y-3 rounded-lg border p-4">
        <h2 className="text-lg font-medium">{messages.auth.account}</h2>
        <AccountSettings inline />
      </section>
      <section className="space-y-3 rounded-lg border p-4 md:col-span-2">
        <h2 className="text-lg font-medium">{m.paperless}</h2>
        <p className="text-sm text-muted-foreground">{m.operatorScope}</p>
        <p role="status">{diagnosis}</p>
        <Button type="button" variant="outline" disabled={status.isFetching} onClick={() => void status.refetch()}>
          {m.checkAgain}
        </Button>
      </section>
    </div>
  </main>
}
