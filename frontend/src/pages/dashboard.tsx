import { AlertTriangle, Database, Server, ShieldCheck } from 'lucide-react'

import { useHealth, useInfo } from '@/api/queries'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { messages } from '@/i18n/messages'

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between border-b border-border/60 py-1.5 text-sm last:border-0">
      <span className="text-muted-foreground">{label}</span>
      <span className="tabular font-medium">{value}</span>
    </div>
  )
}

export function DashboardPage() {
  const health = useHealth()
  const info = useInfo()

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <div>
        <h1 className="text-xl font-semibold">{messages.dashboard.title}</h1>
        <p className="text-sm text-muted-foreground">
          {messages.dashboard.subtitle}
        </p>
      </div>

      {info.isSuccess && !info.data.paperless_configured && (
        <Card className="border-warning/40 bg-warning/5">
          <CardHeader className="flex-row items-center gap-2 space-y-0">
            <AlertTriangle className="h-4 w-4 text-warning" aria-hidden="true" />
            <CardTitle>{messages.dashboard.notConfiguredTitle}</CardTitle>
          </CardHeader>
          <CardContent className="text-sm text-muted-foreground">
            {messages.dashboard.notConfiguredBody}
          </CardContent>
        </Card>
      )}

      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardHeader className="flex-row items-center gap-2 space-y-0">
            <Server className="h-4 w-4 text-primary" aria-hidden="true" />
            <CardTitle>{messages.system.title}</CardTitle>
          </CardHeader>
          <CardContent>
            {health.isError ? (
              <p className="text-sm text-destructive">{messages.errors.network}</p>
            ) : health.isPending ? (
              <p className="text-sm text-muted-foreground">
                {messages.status.checking}
              </p>
            ) : (
              <>
                <Row label={messages.system.backend} value={health.data.status} />
                <Row label={messages.system.database} value={health.data.database} />
                <Row label={messages.system.version} value={health.data.version} />
              </>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex-row items-center gap-2 space-y-0">
            <Database className="h-4 w-4 text-primary" aria-hidden="true" />
            <CardTitle>{messages.system.paperless}</CardTitle>
          </CardHeader>
          <CardContent>
            {info.isSuccess ? (
              <>
                <Row
                  label={messages.system.apiVersion}
                  value={String(info.data.paperless_api_version)}
                />
                <Row
                  label={messages.system.concurrency}
                  value={String(info.data.max_concurrency)}
                />
                <Row
                  label={messages.system.pageSize}
                  value={String(info.data.default_page_size)}
                />
              </>
            ) : (
              <p className="text-sm text-muted-foreground">
                {messages.status.checking}
              </p>
            )}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader className="flex-row items-center gap-2 space-y-0">
          <ShieldCheck className="h-4 w-4 text-success" aria-hidden="true" />
          <CardTitle>{messages.safety.readOnlyNow}</CardTitle>
        </CardHeader>
        <CardContent>
          <CardDescription>{messages.safety.dryRunDefault}</CardDescription>
        </CardContent>
      </Card>
    </div>
  )
}
