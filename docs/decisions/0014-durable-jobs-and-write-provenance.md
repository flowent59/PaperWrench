# ADR-0014: Durable Jobs, coordinated execution and uncertain write outcomes

## Status

Accepted for M8. Supersedes the monolithic target snapshot and SSE choices in
ADR-0003/0006, and the future transactional handoff in ADR-0013. Qualifies
ADR-0005/0012: database uniqueness does not establish exactly-once HTTP delivery.
No rollback execution is introduced.

M9 extends this execution contract with linked rollback Jobs in
[ADR-0015](0015-safe-rollback-jobs.md); provenance and recovery remain unchanged.

## Analysis of the existing implementation

1. `Job` and `JobOperation` are dormant M0 tables. No scheduler, recovery or
   History exists. Keep the Job lifecycle, but make document execution explicit.
2. `Job.document_ids_json` cannot efficiently claim/page individual targets.
   Replace it with indexed `JobTarget` rows; field audit stays in `JobOperation`.
3. M7 `confirm()` commits token consumption alone. Extract its claim into a
   caller-owned Session so Apply can claim, create Job and copy all staged rows
   in one SQLite transaction. The existing review-only route remains review-only.
4. M6 proposes fields independently; M5 already merges them into one document
   PATCH. Audit fields independently, but execute the whole document together.
5. The lifecycle client owns M5's document coordinator. Generalize that boundary
   with a fresh-document planning callback and a durable pre-send callback.
6. A PATCH may succeed without a recorded response. Persist `writing` before
   sending; recovery turns it into `ambiguous`, never inferred success.
7. M5 only returns the PATCH document. Jobs additionally GET under the same lock
   and compare response/readback affected values before recording provenance.
8. Boot currently has no Job recovery. All pending/running Jobs become
   interrupted; explicit resume schedules only pending targets. No boot writes.
9. The heartbeat currently only logs ownership loss. Fail closed on loss/error,
   check ownership again before each send, drain in-flight attempts, then stop.
10. Settings already specify concurrency 4, hard ceiling 16. A fixed worker
    pool shared by all Jobs bounds execution and memory independently of size.

## Durable model and confirmation

Job stores transformation operations, original DatasetQuery (or explicit source
kind), preview fingerprints, acknowledgement and timestamps. Explicit IDs live
only in target rows. Each target stores exact ID, position, staged proposal,
execution state and the fresh custom collection when writing. Operations store
field identity and before/intended/written values. No credential or raw upstream
error body is persisted. Targets outlive preview expiry and are never reselected.

Apply conditionally consumes the hashed, unexpired, unused token and copies
staging in bounded batches within one local transaction. No HTTP occurs there.
Failure rolls back everything; replay cannot create another Job. Old M7 rows
without execution preconditions require a fresh preview. Already reviewed M7
tokens cannot execute. Legacy dormant Job targets are preserved as manual-review
evidence by migration; they cannot acquire new provenance through migration.

## Preconditions, mutation and provenance

New previews bind the complete normalized document revision and custom-field
catalogue revision into their result hash. This conservative precondition rejects
even unrelated document changes, including modified time and custom neighbours;
collection order is normalized as in M5. No full document/OCR is persisted.
Before mutation, refresh metadata, hold M5's document lock, GET current state,
check permissions/deletion and reevaluate with M6. Relevant intended values must
still match the reviewed proposal. If every affected field already equals its
reviewed intended value, record unchanged with no PATCH and no provenance.
Otherwise require the exact preview revision/catalogue and compatible proposals.
Mixed changed/unchanged fields share one coherent PATCH containing changed core
fields and a complete merge of custom fields. No independent field PATCHes.

Commit fresh before values and a `writing` marker immediately before sending.
The callback also rechecks runtime ownership; no network await is inside that
transaction. `before_value` means this fresh validated decision state, while the
preview before remains in target evidence. `intended_value` is M6's reviewed
proposal. `written_value` comes only from GET after acknowledged PATCH, with
affected response/readback values agreeing (normalization is allowed). A response
or readback failure/disagreement is ambiguous. Intended is never copied to written.
Unchanged fields have no written value or rollback provenance.

The external GET/PATCH race remains: Paperless 3.1.2 provides no demonstrated
atomic precondition (ADR-0012). Local serialization and prompt sending reduce the
window; readback cannot reconstruct overwritten external changes or prove that
no external writer independently wrote the same value. Custom writes require
explicit acknowledgement and operator quiescence remains ASSUMED.

## State, recovery and concurrency

Target: pending -> reading -> writing -> succeeded / ambiguous; reading can also
end unchanged / conflict / permission / missing / failed. Writing with a definite
400/401/403/404/409/422 rejection is classified without retry; transport errors,
5xx, unexpected responses and cancellation after the marker are ambiguous.
No automatic mutation retry, including 429. M8 also performs one read attempt;
transient pre-write failures are visible, not silently retried.

Operation: pending -> succeeded / skipped_unchanged / skipped_conflict /
skipped_permission / skipped_missing / failed / ambiguous. Only succeeded with
verified written value is a future rollback candidate, never authorization.

Job: pending -> running -> completed (all succeeded/unchanged), partial (some
success/unchanged and some negative outcomes), failed (no successful targets).
Any ambiguous outcome yields partial/manual review. Pending work after shutdown
or ownership loss yields interrupted. Boot resets reading to pending (no send),
writing to ambiguous and pending/running Jobs to interrupted. Resume is explicit,
never retries terminal/ambiguous targets, and revalidates every remaining target.
Reserved cancelled status is retained for compatibility; no cancellation feature.

A fixed pool of 1..16 workers claims one durable target at a time across Jobs;
the coordinator serializes same-document writers, including Inspector, and its
shared semaphore also bounds concurrent mutations across those writers. Ownership
is checked at claim and just before PATCH. Loss is latched until process restart.
Already-sent requests may finish and retain honest evidence; shutdown cancels
remaining waits, recording uncertainty according to the durable marker. A forced
takeover cannot fence HTTP already in flight; this is an explicit limitation.
Runtime acquisition is an atomic conditional SQLite upsert; refresh/release are
conditional on the holder. Target claims and pre-send transitions acquire the
SQLite write lock before checking ownership. The execution owner prevents an old
worker from overwriting a target already recovered by a successor.

## History and scale

Server pagination bounds Jobs, targets and field operations to at most 250 rows.
Summary counts come from indexed durable target states, not browser memory.
The UI polls active Jobs and supports refresh/reconnect and explicit resume.
No SSE subsystem is necessary for this MVP. The immutable preview JSON per target
is bounded by M7; neither execution nor History loads all targets/operations.
Confirmation performs O(targets * fields) local inserts in one atomic transaction;
large confirmations can briefly block SQLite writers. This is a measured tradeoff,
not a claim of tested 100,000-document live capacity.
