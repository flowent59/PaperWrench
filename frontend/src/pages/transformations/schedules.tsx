import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'

import { schedulesApi } from '@/api/client'
import type { ScheduleRecurrence } from '@/api/types'
import { Button } from '@/components/ui/button'
import { messages } from '@/i18n/messages'
import { errorMessage } from '@/i18n/errors'
import { formatDateTime } from '@/i18n/format'

interface ApprovalAction { (recurrence: ScheduleRecurrence): Promise<void> }

export function ScheduleApproval({ disabled, approve }: { disabled: boolean; approve: ApprovalAction }) {
  const m = messages.schedules
  const [timezone, setTimezone] = useState(Intl.DateTimeFormat().resolvedOptions().timeZone)
  const [frequency, setFrequency] = useState<'daily' | 'weekly'>('daily')
  const [time, setTime] = useState('09:00')
  const [weekday, setWeekday] = useState(0)
  const [consent, setConsent] = useState(false)
  return <fieldset className="space-y-3 rounded border p-3" disabled={disabled}>
    <legend className="font-medium">{m.approveTitle}</legend>
    <p className="text-sm">{m.policy}</p>
    <div className="flex flex-wrap gap-3">
      <label>{m.timezone}<input className="ml-2 rounded border p-1" value={timezone}
        onChange={(e) => { setTimezone(e.target.value); setConsent(false) }} /></label>
      <label>{m.frequency}<select className="ml-2 rounded border p-1" value={frequency}
        onChange={(e) => { setFrequency(e.target.value as 'daily' | 'weekly'); setConsent(false) }}>
        <option value="daily">{m.daily}</option><option value="weekly">{m.weekly}</option>
      </select></label>
      <label>{m.time}<input className="ml-2 rounded border p-1" type="time" value={time}
        onChange={(e) => { setTime(e.target.value); setConsent(false) }} /></label>
      {frequency === 'weekly' && <label>{m.weekday}<select className="ml-2 rounded border p-1"
        value={weekday} onChange={(e) => { setWeekday(Number(e.target.value)); setConsent(false) }}>
        {Object.values(m.days).map((day, index) => <option key={day} value={index}>{day}</option>)}
      </select></label>}
    </div>
    <label className="flex items-start gap-2 text-sm"><input type="checkbox" checked={consent}
      onChange={(e) => setConsent(e.target.checked)} />{m.consent}</label>
    <Button disabled={disabled || !consent || !timezone.trim() || !time} onClick={() => {
      const [hour = 9, minute = 0] = time.split(':').map(Number)
      void approve({ timezone, frequency, weekday, hour, minute })
    }}>{m.enable}</Button>
  </fieldset>
}

export function Schedules() {
  const m = messages.schedules
  const cache = useQueryClient()
  const [selected, setSelected] = useState<number | null>(null)
  const [before, setBefore] = useState<number | undefined>()
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const schedules = useQuery({ queryKey: ['schedules'], queryFn: schedulesApi.list, refetchInterval: 5000 })
  const runs = useQuery({ queryKey: ['schedule-runs', selected, before], enabled: selected !== null,
    queryFn: () => schedulesApi.runs(selected!, before), refetchInterval: 5000 })
  const status = (value: string) => m.statuses[value as keyof typeof m.statuses] ?? value
  async function action(id: number, kind: 'disable' | 'acknowledge') {
    setBusy(true); setError('')
    try {
      await schedulesApi[kind](id)
      await cache.invalidateQueries({ queryKey: ['schedules'] })
    } catch (cause) { setError(errorMessage(cause)) }
    finally { setBusy(false) }
  }
  return <section className="space-y-3 rounded border p-3" aria-label={m.title}>
    <h3 className="font-medium">{m.title}</h3>
    <p className="text-sm">{m.help}</p>
    {(error || schedules.isError) && <p role="alert">{error || errorMessage(schedules.error)}</p>}
    {schedules.data?.length === 0 && <p>{m.empty}</p>}
    {schedules.data?.map((item) => <div key={item.id} className="space-y-2 rounded border p-3">
      <p>{messages.rules.revision} {item.rule_revision} · {m.rule} #{item.rule_id} · {status(item.status)}</p>
      <p>{item.recurrence.frequency === 'daily' ? m.daily : m.weekly}
        {item.recurrence.frequency === 'weekly' ? ` (${Object.values(m.days)[item.recurrence.weekday]})` : ''}
        {' '}{String(item.recurrence.hour).padStart(2, '0')}:{String(item.recurrence.minute).padStart(2, '0')}
        {' '}{item.recurrence.timezone}</p>
      {item.enabled && <p>{m.next}: {formatDateTime(item.next_run_at)}</p>}
      {item.notification && <div role="alert" className="rounded border border-destructive p-2">
        <p>{m.failure} ({item.notification})</p>
        <Button disabled={busy} variant="outline" onClick={() => void action(item.id, 'acknowledge')}>{m.dismiss}</Button>
      </div>}
      <div className="flex gap-2">
        <Button disabled={busy || !item.enabled} variant="outline"
          onClick={() => void action(item.id, 'disable')}>{m.disable}</Button>
        <Button variant="outline" onClick={() => { setSelected(item.id); setBefore(undefined) }}>{m.history}</Button>
      </div>
      {selected === item.id && <div>
        {runs.isError && <p role="alert">{errorMessage(runs.error)}</p>}
        {runs.data?.length === 0 && <p>{m.noRuns}</p>}
        <ol className="space-y-2">{runs.data?.map((run) => <li key={run.id}>
          {formatDateTime(run.scheduled_for)} · {status(run.status)}
          {run.error_code && <span> ({run.error_code})</span>}
          {run.job_id !== null && <a className="ml-2 text-primary underline" href={`/jobs/${run.job_id}`}>{m.openJob}</a>}
        </li>)}</ol>
        {runs.data?.length === 50 && <Button variant="outline"
          onClick={() => setBefore(runs.data?.at(-1)?.id)}>{m.older}</Button>}
      </div>}
    </div>)}
  </section>
}
