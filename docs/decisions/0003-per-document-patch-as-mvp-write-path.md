# ADR-0003: Per-document PATCH as the MVP write path

## Status

Accepted (M0).

## Context

The reference scenario for the MVP is renaming several hundred documents of
type `Relevé de vacations` from a template built out of their custom fields.
There are two ways to do that against the Paperless API, and they are not
equivalent.

Paperless exposes `POST /api/documents/bulk_edit/`, which takes a list of
document ids and a method. It is the obvious candidate for a bulk tool, and it
is the wrong one here, for three independent reasons found during the review:

1. **It cannot do the job.** The available methods cover tags, correspondent,
   document type, storage path, permissions, merge, split, rotate, delete and
   custom fields - but there is no `set_title`. The single most important MVP
   transformation is not expressible.
2. **It computes one value for all documents.** `bulk_edit` applies the same
   change to every id. A template rename produces a *different* title per
   document, derived from that document's own fields. The primitive does not
   fit the operation.
3. **It is asynchronous and opaque.** The call returns once the task is
   queued. There is no per-document result, so we cannot know which documents
   succeeded, which were skipped, and what each one's value was immediately
   before the write - which is precisely the information a preview, a conflict
   report and a rollback are made of.

Per-document `PATCH /api/documents/{id}/` gives us the opposite trade: it is
slower and chattier, but each call is synchronous, returns the resulting
document, and can be preceded by a read of that specific document.

## Decision

For the MVP, every write is an individual `PATCH /api/documents/{id}/`, executed
through a job engine with the following properties.

**Bounded concurrency.** Writes run through an `asyncio.Semaphore`, default 4
concurrent requests, configurable via `PAPERWRENCH_MAX_CONCURRENCY` with a hard
ceiling of 16. The ceiling is not advisory: users must not be able to
accidentally saturate their own Paperless instance from a config file. The
default is deliberately conservative because the typical target is a small
self-hosted instance sharing a CPU with everything else.

**Verify before write.** Immediately before each PATCH, PaperWrench re-reads
the document and checks that the field's current value still equals the
`before_value` captured during the preview. If it differs, the document is not
written: the operation is recorded as `SKIPPED_CONFLICT` and reported. This is
forward conflict detection, and it operates per document.

This is complementary to, not replaced by, the `preview_token` mechanism. The
token guarantees workflow integrity - that the confirmation the user clicked
corresponds to the preview they actually saw, and not to a stale browser tab.
The per-document revalidation guarantees data integrity against changes made
between the preview and the write. Neither subsumes the other, and both are
enforced.

**Verify after write.** The PATCH response is inspected and the value Paperless
actually stored is recorded as `written_value`, which may differ from what we
intended (see ADR-0005).

**Durable per-document record.** Every attempt produces a `JobOperation` row,
committed as it completes, carrying `before_value`, `intended_value`,
`written_value`, a snapshot of the document's custom fields, and a status. A
uniqueness constraint on `(job_id, document_id, field_kind, field_key)` makes
resumption idempotent: a job interrupted mid-flight can be resumed without
rewriting anything it already wrote.

**Materialised target set.** The document ids a job will act on are computed
once, at job creation, and stored in the job row. The job never re-evaluates
its FilterSet during execution. A filter is a moving target - a document
modified by the job's own earlier writes may drop out of it - and a job whose
scope changes while it runs cannot be previewed, resumed or rolled back
honestly. The originating FilterSet is kept alongside for explanation in the
history, not for re-execution.

## Consequences

The operation is slow and honest: 300 documents means 300 reads and up to 300
writes, roughly 600 requests, minutes rather than seconds at concurrency 4.
That is acceptable for an operation the user runs deliberately and watches.

In exchange we get exactly what a destructive bulk tool needs: per-document
success and failure, per-document conflict detection, an accurate preview, a
resumable job, and a rollback that can refuse individual documents whose value
has since moved.

Partial failure becomes a first-class outcome rather than an error state. A job
can finish `PARTIAL` with a precise report, which is far more useful than an
all-or-nothing result that leaves the user guessing.

The cost is load on the user's Paperless instance, which is why concurrency is
bounded and the default is low.

`bulk_edit` is not banned forever. It remains a legitimate post-MVP
optimisation for the operations it actually supports and where all documents
receive the same value - adding a tag to a selection, for example. Adopting it
requires reconciling it with per-document history and rollback, which is a
design problem to solve then, not now.

## Alternatives considered

**`bulk_edit` for everything.** Rejected: cannot set titles, cannot vary the
value per document, and returns no per-document result.

**Unbounded concurrency.** Rejected: trivially overwhelms a small Paperless
instance, and the failure mode - timeouts partway through a bulk write - is the
worst one available.

**Sequential writes, no concurrency.** Rejected as the default: it makes a
several-hundred-document job unpleasantly slow for no safety gain, since the
safety comes from per-document verification, not from serialisation. Setting
`PAPERWRENCH_MAX_CONCURRENCY=1` remains available.
