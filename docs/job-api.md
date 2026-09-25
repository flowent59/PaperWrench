# Job and History API (M8)

All paths start with `/api/v1`. Responses use `Cache-Control: no-store` and the
normal error envelope. No endpoint runs a rollback or cancellation.

## Explicit Apply

`POST /jobs` returns 201 with a Job summary. Body: M7 confirmation fields
(`preview_token`, `transformation`, `target_fingerprint`, `result_fingerprint`,
`version: 1`, `acknowledge: true`) plus `preview_id` and
`acknowledge_external_race: true` when any custom field is targeted.

The unexpired unused token is claimed and exact staged targets/field operations
are inserted in one SQLite transaction. Failure before commit leaves the token
usable and no partial Job. Duplicate confirmation returns 409 `PREVIEW_STALE`.
No filter is reevaluated. No HTTP request occurs inside the transaction.
Execution begins after commit and does not depend on a browser connection.
A lost Apply response may mean the Job exists: inspect History before creating
another preview. Replaying the same token cannot create another Job.

`POST /previews/{id}/confirm` retains M7's review-only semantics; it cannot later
execute. Apply uses the shared claim primitive, never review followed by a
separate Job insert. Pre-M8 previews without document/catalogue revisions need
a new preview. Expiring/discarding staging cannot delete a durable Job.

## Read endpoints

| Endpoint | Contents |
| --- | --- |
| `GET /jobs?page=1&page_size=25` | Jobs, newest first |
| `GET /jobs/{id}` | Durable summary, specification and original selection |
| `GET /jobs/{id}/targets?page=1&page_size=25&status=ambiguous` | Stable positions; optional status filter |
| `GET /jobs/{id}/operations?page=1&page_size=25&document_id=123` | Field audit, optionally one document |

Page envelopes are `{items, page, page_size, total, page_count}`. Allowed sizes
are 25, 50, 100 and 250; there is no fetch-all mode. Explicit IDs appear in target
pages, not in a duplicated giant Job JSON array. `dataset_query` retains the
original DatasetQuery for explanation only. Job summary `operations` is the
transformation specification (at most 100 fields), not the field audit.

`counts` groups persisted targets: pending, reading, writing, succeeded,
unchanged, conflict, permission, missing, failed, ambiguous. `processed` excludes
pending/reading/writing. Targets/operations include attempts, timestamps,
sanitized error code and known HTTP status; raw upstream errors are not retained.

Operation `before` is the fresh validated decision value (null if execution never
reached that point), `intended` is the reviewed M6 result, and `written` is a
post-PATCH GET value consistent with the acknowledged response. Preview evidence
remains on the target. Unchanged/conflicted/ambiguous operations never acquire a
manufactured written value. `rollback_candidate` is informational and cannot
authorize restoration, which remains M9.

## Recovery and resume

`POST /jobs/{id}/resume` accepts only interrupted Jobs with pending unsent targets.
Completed, failed, conflicted, missing, denied, unchanged and ambiguous targets
are never replayed. The UI never resumes on load.
`PAPERWRENCH_AUTO_RESUME_JOBS=true` is rejected in M8.

On boot: reading -> pending; writing -> ambiguous; pending/running Jobs with
remaining work -> interrupted; fully resolved Jobs are aggregated. A crash before
local result commit never becomes success from `current == intended`.
Runtime loss stops scheduling and new sends. Already-sent requests can finish
and retain evidence; an old worker cannot overwrite a recovered target.

## Limits and guarantees

One process, SQLite WAL, default concurrency 4, hard ceiling 16. The shared M5
coordinator's semaphore also bounds Inspector/low-level mutations on the same
lifecycle client. No transaction spans HTTP. No automatic HTTP write retries.
Definite 400/401/403/404/409/422/429 responses are recorded without retry;
transport loss/5xx/unexpected response or failed readback after send is ambiguous.

Document revisions are conservative: any normalized document change (including
modified time, permissions, source values and neighbours) prevents a write,
unless all affected fields already equal their reviewed targets: then no PATCH
is sent. Catalogue changes also conflict. Fresh M6 evaluation must still agree
with preview. Paperless's external GET/PATCH race remains (ADR-0012).

History uses indexed filters and offset pagination; deep pages incur offset
scanning. Confirmation takes one O(targets × operations) transaction in batches
of 100. It can block local writes and the event loop for a large snapshot.
The synthetic 10k test checks bounded ORM batches and pages, not live 100k
capacity or an execution SLA.
