# Scheduled rules

Issue #51 adds opt-in daily and weekly schedules to [saved rules](automation-rules.md).
In **Transformations → Saved rules**, select a rule, generate and review its preview,
acknowledge any clearing/custom-field risks, then approve unattended execution with
a local time and an IANA timezone. A zero-change preview can approve a schedule:
future matching documents may need changes. One schedule exists per rule.

## Authorization and credentials

Approval consumes a single-use preview token and records the owner, rule revision,
resolved transformation, preview ID and approval time. Every occurrence revalidates
the Paperless credential, the rule, collection and current document permissions,
and generates a fresh preview before entering the existing job engine.
Filter approval includes future documents matching that exact filter. Changes to
a rule revision, a dynamic collection's filter or a static collection's membership
require another reviewed approval. There is no account-wide or cross-user schedule.

Session-bound execution is the default. The session that approved the schedule must
still be active when it runs. Logout, expiry and restart require a fresh approval.

For autonomous execution, remember a token in Account settings, review the rule
preview and grant the additional consent on that schedule. This uses the encrypted
credential vault and the verified Paperless identity. Authorization covers future
matching documents until disabled. After restart, only future occurrences run.
Replacing or deleting the remembered credential pauses its autonomous schedules.
A rejected credential pauses execution and appears in the owner's notifications.
Disable a schedule to revoke future execution while retaining its job history.

Operators can set `PAPERWRENCH_AUTONOMOUS_SCHEDULES=false` and restart to disable
this mode globally. The schedule API accepts `autonomous: true` only with the
usual approval and a remembered credential. Existing schedules use session mode.

## Recurrence and failures

- Times are local to the selected IANA timezone, stored as UTC instants for dispatch.
  A nonexistent daylight-saving time is skipped. A repeated time runs only at its
  first occurrence. Monday is weekday 0; Sunday is 6.
- The dispatcher checks approximately every five seconds while the app is running.
  If delayed, it starts at most one due occurrence and advances to the next future
  calendar time. It does not replay every missed interval.
- Only the process owning the SQLite runtime lock may dispatch. An occurrence is
  claimed under `BEGIN IMMEDIATE`, with a unique `(schedule_id, scheduled_for)` key.
  Job creation and the occurrence's job link commit atomically. No occurrence is
  automatically replayed after a crash, including a crash before job dispatch.
- Only one occurrence per schedule may prepare or run. Existing unfinished jobs
  also block overlapping document targets, including manual jobs. Skipped or failed
  occurrences remain visible in history.
- Read failures caused by Paperless downtime do not write. There is no immediate
  retry loop; after failure, the next calendar occurrence is the next attempt.
  Failed or uncertain writes are never automatically retried.
  A failed, partial or interrupted job pauses the schedule for review and reapproval.
  Existing job recovery and rollback retain before/written values and verification.
- Disabling prevents future dispatch and blocks any preparation that has not yet
  committed a job. A job already committed continues under its existing authorization.

## Monitoring and API

The **Scheduled rules** panel shows status, recurrence, next occurrence, durable
failure notifications and paginated execution history. Notifications remain until
acknowledged or a new schedule approval; delivery is in-app, without email or webhooks.
Each writing occurrence links to its job for details, recovery and rollback.

All endpoints require the current user's session; mutations require CSRF protection:

| Endpoint | Purpose |
| --- | --- |
| `GET /api/v1/schedules` | List owned schedules, including disabled schedules |
| `POST /api/v1/schedules` | Approve/create or replace a rule's schedule |
| `POST /api/v1/schedules/{id}/disable` | Stop future dispatch |
| `POST /api/v1/schedules/{id}/acknowledge` | Clear the current notification |
| `GET /api/v1/schedules/{id}/runs?before={run_id}` | Newest-first history, 50 rows per page |

Approval accepts `rule_id`, `recurrence` (`timezone`, `frequency`, `hour`, `minute`,
`weekday`), `acknowledge_unattended: true`, and `preview` containing the normal
`CreateJob` confirmation. No write occurs during approval. Reusing the preview,
changing its transformation, or approving another owner's rule is rejected.
