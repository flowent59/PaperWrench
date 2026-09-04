import { AlertTriangle, Database, Server, ShieldCheck } from 'lucide-react'

import { useHealth, useInfo, usePaperlessStatus } from '@/api/queries'
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
  const paperless = usePaperlessStatus()

  const probe = paperless.data
  // Only one banner at a time, in decreasing order of "the user must fix this
  // before anything else works".
  const banner =
    probe === undefined
      ? null
      : !probe.configured
        ? {
            title: messages.dashboard.notConfiguredTitle,
            body: messages.dashboard.notConfiguredBody,
          }
        : !probe.connected
          ? {
              title: messages.dashboard.unreachableTitle,
              // The backend already scrubs secrets out of this message before
              // it is serialised, so it is safe to render verbatim.
              body: probe.error_message ?? messages.errors.generic,
            }
          : !probe.compatible
            ? {
                title: messages.dashboard.incompatibleTitle,
                body: probe.error_message ?? messages.errors.generic,
              }
            : null

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <div>
        <h1 className="text-xl font-semibold">{messages.dashboard.title}</h1>
        <p className="text-sm text-muted-foreground">
          {messages.dashboard.subtitle}
        </p>
      </div>

      {banner !== null && (
        <Card className="border-warning/40 bg-warning/5">
          <CardHeader className="flex-row items-center gap-2 space-y-0">
            <AlertTriangle className="h-4 w-4 text-warning" aria-hidden="true" />
            <CardTitle>{banner.title}</CardTitle>
          </CardHeader>
          <CardContent className="text-sm text-muted-foreground">
            {banner.body}
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
            {probe !== undefined ? (
              <>
                {probe.url !== null && (
                  <Row label={messages.system.url} value={probe.url} />
                )}
                <Row
                  label={messages.system.paperlessVersion}
                  value={probe.paperless_version ?? messages.status.unknown}
                />
                <Row
                  label={messages.system.apiVersion}
                  value={
                    probe.requested_api_version === null
                      ? messages.status.unknown
                      : String(probe.requested_api_version)
                  }
                />
                {/* Labelled "highest", not "current": Paperless reports its
                    maximum here regardless of what we negotiated (ADR-0008). */}
                <Row
                  label={messages.system.maxApiVersion}
                  value={probe.api_version ?? messages.status.unknown}
                />
                {probe.document_count !== null && (
                  <Row
                    label={messages.system.documents}
                    value={String(probe.document_count)}
                  />
                )}
              </>
            ) : info.isSuccess ? (
              <Row
                label={messages.system.apiVersion}
                value={String(info.data.paperless_api_version)}
              />
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
