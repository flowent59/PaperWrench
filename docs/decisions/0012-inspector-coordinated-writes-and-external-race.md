# ADR-0012: Coordinated document writes and the external-writer race

## Status

Accepted for M5. Supersedes the deferred concurrency decision in ADR-0004
and qualifies ADR-0003/0005 for immediate Inspector edits. Bulk Jobs and durable
history remain M8; rollback remains M9.

## Decision

The existing lifecycle PaperlessClient owns a DocumentMutationCoordinator.
Every public document write uses its per-document asyncio lock. Inspector and
future Jobs must use this client, or explicitly share its coordinator.
In `mutate_document`, the lock covers the fresh GET, permission/precondition checks, complete merge,
single PATCH and response capture. Waiting/cancelled callers cannot strand a
lock; unused entries are removed. Different documents can proceed independently.
This is a single-process contract, consistent with the existing deployment.
Separate deployments/databases or forced runtime-lock takeover are external actors.

Inspector sends an opaque revision of the complete normalized document it saw.
Under the lock, any difference (including unrelated custom fields or modified
time) rejects the request with non-retryable 409 and zero PATCHes. This is
deliberately conservative for manual edits. JSON types, ABSENT, NULL, false,
zero and empty strings remain distinct. Order of tags/custom entries is ignored.
One combined core/custom change generates one PATCH, never one per field.

The client core-only helper accepts a closed allowlist, rejecting arbitrary keys
and aliases before I/O. Custom changes are operations merged into the freshly
read complete collection; caller-supplied replacement arrays are not exposed.
Unknown definitions are preserved. Monetary strings never pass through float;
select identity is the option ID. The response records before, intended and
actual values from Paperless, including its normalization.

## What this does not guarantee

Fresh reads and local locks provide **no atomicity against external writers**.
An external REST client can write after our GET and before our PATCH; its changes
can be lost. A final GET cannot recover that overwritten information. A mutex in
Redis would not make Paperless's own UI or consumers participate either.
We do not claim compare-and-swap, exactly-once delivery or durable rollback.

Custom-field writes fail closed without explicit `acknowledge_external_race=true`.
The UI requires a per-save unchecked acknowledgement explaining the possible
loss and asking the operator to pause other writers. This is informed acceptance
of a residual risk, **not proof** that they are paused. Operators needing atomic
external concurrency must not enable these edits. Core-field edits also have a
same-field external race; they do not replace the custom-field collection.
No write is retried automatically, including a timeout with an unknown outcome.
Reload and inspect before making a new decision.

## Alternatives and evidence

- A fresh GET alone: rejected; M2's VERIFIED_LIVE interleaving already loses data.
- Local serialization plus stale-state rejection: chosen, tested deterministically
  with two cooperating writers. No infrastructure beyond the current process.
- Distributed locks: rejected for the MVP and unable to coordinate external actors.
- ETag/If-Match: no supported atomic precondition established by the current API;
  VERIFIED_LIVE on 3.1.2: impossible If-Match plus an old If-Unmodified-Since
  still applies PATCH (test_inspector_live.py).
- Bulk edit: asynchronous with no synchronous per-document result (ADR-0003).
- Direct database transactions or a Paperless fork: forbidden by ADR-0002.

Mocked tests prove our boundary and serialization (VERIFIED_SOURCE for our code).
Guarded live tests now prove preservation, normalization, permissions, stale
rejection and deterministic local/external interleaving on 3.1.2 (VERIFIED_LIVE).
Unexecuted probes are NOT_RUN, never VERIFIED_LIVE. Operator quiescence remains
ASSUMED, never machine-verified.

The core-only `update_document` and compatibility `update_custom_fields` are
low-level helpers: they serialize, validate the boundary and defer permissions
to Paperless; only the custom helper has the optional legacy collection snapshot.
They are not Inspector/Jobs orchestration APIs. New application writers must use
`mutate_document` with its required revision and fail-closed permission preflight.
