import { Moon, Sun } from 'lucide-react'

import { useHealth, useInfo } from '@/api/queries'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { useTheme } from '@/components/theme-provider'
import { messages } from '@/i18n/messages'

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
  const info = useInfo()

  if (info.isPending || info.isError) {
    return <Badge variant="outline">{messages.status.unknown}</Badge>
  }
  // M0 only knows whether credentials are present; the real reachability
  // probe (GET /system/paperless) lands in M1.
  return info.data.paperless_configured ? (
    <Badge variant="outline">{messages.status.unknown}</Badge>
  ) : (
    <Badge variant="warning">{messages.status.notConfigured}</Badge>
  )
}

export function Header() {
  const { theme, toggleTheme } = useTheme()

  return (
    <header className="flex h-14 shrink-0 items-center justify-between gap-4 border-b border-border bg-background px-4">
      <div className="flex items-center gap-3 text-sm text-muted-foreground">
        <span className="hidden sm:inline">{messages.safety.dryRunDefault}</span>
      </div>

      <div className="flex items-center gap-3">
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
      </div>
    </header>
  )
}
