# ADR-0015: Safe rollback as a linked durable Job

## Status

Accepted for M9, issue #12. Extends ADR-0014 and implements ADR-0005's rollback
intent subject to ADR-0012's external-writer race. M10 is outside this change.

## Decision

Rollback creates a new `Job(type=rollback, rollback_of_job_id=original.id)` and
new operations with `rollback_of_operation_id`. Original operations, values,
statuses and timestamps are never rewritten. Existing schema links suffice.

Only terminal transformation Jobs can be reviewed. An operation is eligible
only when succeeded, attempted, and carrying both a fresh before and a verified
written value that differ. Ambiguous/unknown, failed and unchanged observations
are excluded, even if current equals intended. Legacy records lacking evidence
are excluded. A rollback of a rollback is outside this milestone.

The shared M7 preview builder walks original durable targets in batches of 100,
stages per-document restoration specs and original-operation IDs, and exposes
the same paginated preview API. It shares M7's active-builder guard, expiry,
cleanup, byte/document/time limits and hashed single-use capability. Preview
summaries bind the original Job. Forward confirmation rejects rollback previews.

Preview refreshes metadata, reads Paperless, and compares each candidate's typed
current value to **original written**, never intended. A difference blocks all
candidate fields of that document. Other documents remain eligible; preview
errors do not block their explicit confirmation. Missing/invisible documents,
permission failures and manual-review exclusions are shown. At least one changed
document is required. Rejected preview documents remain terminal audit targets
in the rollback Job; they are never scheduled later merely because state changes.

Confirmation validates capability, expiry, version, fingerprints, original Job
and race acknowledgement. `BEGIN IMMEDIATE` serializes token consumption,
original-Job validation and bounded target/operation adoption in one transaction.
**One rollback Job per original Job**: duplicate tokens, concurrent requests and
distinct previews cannot create another. Replays return 409; inspect the linked
Job after a lost response. Only explicit resume of its unsent work is available.
This conservative rule also applies after failed/partial rollback; retrying
terminal fields is outside this milestone.

## Execution and evidence

The existing JobEngine executes each stored per-document restoration spec using
the same worker pool, coordinator, permission checks, fresh read, complete custom
merge, single PATCH, acknowledged response and GET readback as M8. The only
precondition differences are deliberate: rollback checks candidate values rather
than the whole document revision, and **never uses M8's already-at-target shortcut**.
Thus unrelated later edits are preserved, while even a value already equal to the
desired restoration conflicts when it differs from the original written value.
Metadata changes after preview still fail closed.

`ABSENT` produces removal; `NULL` produces explicit null. Empty string, zero,
false, exact monetary strings/Decimal, Select option ID and date keep the existing
M6 typed representations. The rollback stores its own fresh before, original-before
intention and actual verified written value, including Paperless normalization.

ADR-0014's durable send marker, ambiguity classification, ownership checks and
recovery are unchanged. Unknown PATCH/readback/crash outcomes remain ambiguous;
matching the desired value never proves authorship. Startup sends nothing and
resume never replays a terminal or ambiguous target. The external GET/PATCH race
and in-flight takeover limitation in ADR-0012/0014 remain; operator quiescence is
ASSUMED. No atomic compare-and-swap or exactly-once guarantee is claimed.

## Evidence and consequences

VERIFIED_SOURCE: focused rollback tests cover eligibility, partial Jobs, strict
typed values, grouped conflict/no-PATCH, latest neighbors, normalization,
permissions/deletion, stale/cross-Job/duplicate confirmation, pagination,
runtime loss, crash boundaries, readback disagreement, recovery and secret-free
history. UI tests cover review, acknowledgement, race acknowledgement, pagination,
exclusions and stale/uncertain confirmation. Existing M8 concurrency tests protect
the shared scheduler/coordinator.

VERIFIED_LIVE on disposable Paperless 3.1.2: Dry Run → normalized title/monetary
Job → linked rollback → actual restoration; later custom neighbor preserved;
later title refused without send; removed document refused. See
`tests/backend/live/test_rollback_live.py`. Full capacity at 100,000 targets is
NOT_RUN. Per-field restoration within a conflicting document is deliberately
deferred in favor of one coherent mutation.
