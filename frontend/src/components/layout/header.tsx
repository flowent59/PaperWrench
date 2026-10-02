import { LogOut, Moon, Sun } from 'lucide-react'
import { AccountSettings } from '@/account-settings'

import { useHealth, usePaperlessStatus } from '@/api/queries'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { useTheme } from '@/components/theme-provider'
import { messages } from '@/i18n/messages'
import { useAuth } from '@/auth-context'
import type { Locale } from '@/i18n/messages'

function BackendStatus() {
  const health = useHealth()

  if (health.isPending) {
    return <Badge variant="outline">{messages.status.checking}</Badge>
  }
  if (health.isError) {
    return <Badge variant="destructive">{messages.system.unreachable}</Badge>
  }
  return health.data.status === 'ok' ? (
    <Badge variant="success">{messages.system.healthy}</Badge>
  ) : (
    <Badge variant="warning">{messages.status.degraded}</Badge>
  )
}

function PaperlessStatus() {
  const status = usePaperlessStatus()

  if (status.isPending) {
    return <Badge variant="outline">{messages.status.checking}</Badge>
  }
  // A rejection means the PaperWrench backend itself is unreachable; the probe
  // endpoint reports Paperless failures in its payload, never by throwing.
  if (status.isError) {
    return <Badge variant="outline">{messages.status.unknown}</Badge>
  }

  const { configured, connected, compatible } = status.data
  if (!configured) {
    return <Badge variant="warning">{messages.status.notConfigured}</Badge>
  }
  if (!connected) {
    return <Badge variant="destructive">{messages.status.disconnected}</Badge>
  }
  // Reachable but speaking a version we cannot safely write against. This is
  // deliberately not shown as "connected": every write assumption behind it is
  // unverified, so the honest signal is a failure one.
  if (!compatible) {
    return <Badge variant="destructive">{messages.status.incompatible}</Badge>
  }
  return <Badge variant="success">{messages.status.connected}</Badge>
}

export function Header() {
  const { theme, toggleTheme } = useTheme()
  const { session, locale, changeLocale, logout } = useAuth()

  return (
    <header className="flex h-14 shrink-0 items-center justify-between gap-4 border-b border-border bg-background px-4">
      <div className="flex items-center gap-3 text-sm text-muted-foreground">
        <span className="hidden sm:inline">{messages.safety.dryRunDefault}</span>
      </div>

      <div className="flex items-center gap-3">
        <span className="hidden text-sm text-muted-foreground md:inline">{session.display_name}</span>
        <AccountSettings />
        <select aria-label={messages.locale.language}
          className="rounded border border-input bg-background px-2 py-1 text-xs"
          onChange={(event) => void changeLocale(event.target.value as Locale)} value={locale}>
          <option value="en">{messages.locale.english}</option>
          <option value="fr">{messages.locale.french}</option>
        </select>
        <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <span>{messages.system.backend}</span>
          <BackendStatus />
        </div>
        <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <span>{messages.system.paperless}</span>
          <PaperlessStatus />
        </div>
        <Button
          variant="ghost"
          size="icon"
          onClick={toggleTheme}
          aria-label={messages.theme.toggle}
          title={messages.theme.toggle}
        >
          {theme === 'dark' ? (
            <Sun className="h-4 w-4" aria-hidden="true" />
          ) : (
            <Moon className="h-4 w-4" aria-hidden="true" />
          )}
        </Button>
        <Button variant="ghost" size="icon" onClick={() => void logout()}
          aria-label={messages.auth.signOut} title={messages.auth.signOut}>
          <LogOut className="h-4 w-4" aria-hidden="true" />
        </Button>
      </div>
    </header>
  )
}
