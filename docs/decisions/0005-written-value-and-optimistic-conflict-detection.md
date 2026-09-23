# ADR-0005: `written_value` and optimistic conflict detection

## Status

Accepted (M0).

**M5 qualification:** [ADR-0012](0012-inspector-coordinated-writes-and-external-race.md)
now defines the shared mutation coordinator and explicit external-race contract.
Immediate Inspector edits return before/intended/actual values without durable
history or rollback; the job-specific promises below remain M8/M9 design.

## Context

A rollback has to answer one question per document: *is it still safe to
restore the previous value?* Getting that question wrong in either direction is
harmful. Restoring a document that someone has since edited destroys their
work. Refusing to restore a document that nobody touched makes the undo feature
untrustworthy, which is worse than not having it.

The naive implementation stores the previous value and, on rollback, writes it
back unconditionally. That is a second destructive bulk write with no safety at
all - the exact problem the tool exists to solve, reintroduced by the fix.

The slightly better implementation stores the previous value and the value we
*intended* to write, then rolls back only where the current value still equals
the intended one. That is closer, and it is still wrong, because Paperless does
not always store what you send:

- **Title collisions and normalisation.** Paperless applies its own handling to
  titles; what comes back is not guaranteed to be the string submitted.
- **Monetary fields** are normalised to an ISO-4217 prefixed form: send
  `450` and `EUR450.00` comes back.
- **Dates and datetimes** are re-serialised in Paperless's canonical format and
  timezone.
- **Whitespace and length limits** are applied server-side.
- **Tag matching side effects** can add or remove tags as a consequence of an
  unrelated change.

If any of these apply, `current == intended_value` is false immediately after a
perfectly successful write, and the rollback refuses every document it should
accept.

## Decision

PaperWrench stores three distinct values for every operation, and never
conflates them.

| Field | Meaning | Captured |
| --- | --- | --- |
| `before_value` | The value before PaperWrench touched it | Read immediately before the write |
| `intended_value` | What the transformation computed | At preview time |
| `written_value` | What Paperless actually stored | Read back from the PATCH response |

`written_value` comes from the PATCH response body, not from the request. The
response is the authoritative statement of what Paperless holds.

**Forward conflict detection (before the write).** Immediately before each
PATCH, the document is re-read and the field's current value is compared to
`before_value`. If they differ, someone changed the document between the
preview and now: the write is skipped, the operation is recorded as
`SKIPPED_CONFLICT`, and the document appears in the job report. The user is
told what changed rather than having their change silently overwritten.

**Backward conflict detection (rollback).** A rollback compares the document's
*current* value to `written_value` - not to `intended_value`. If they match,
nothing has touched the document since PaperWrench wrote it, and restoring
`before_value` is safe. If they differ, the document was modified afterwards
and the rollback refuses that document, recording `SKIPPED_CONFLICT`.

This is what makes normalisation harmless: when Paperless rewrites `450` to
`EUR450.00`, `written_value` is `EUR450.00`, and the comparison succeeds
exactly when it should.

**Rollback is a job, not a status.** A rollback creates a new `Job` linked to
the original through `rollback_of_job_id`, producing its own operations with
their own outcomes. Consequences: a rollback can be partial (some documents
restored, others skipped), it can itself be interrupted and resumed, it appears
in history as the operation it is, and it can in principle be rolled back.

**Comparison is type-aware.** Values are compared after normalisation
appropriate to the field kind, not as raw strings, so a formatting difference
that is not a semantic difference does not manufacture a false conflict. A
detected difference always errs towards refusing to write.

Every operation is committed durably as it completes, so a crash between the
PATCH and the local commit leaves at most one operation in an unknown state -
which resumption resolves by re-reading the document rather than assuming.

## Consequences

Rollback is trustworthy in both directions: it does not destroy concurrent
edits, and it does not spuriously refuse documents that are untouched.

Three values per operation is more storage than one. For a 300-document job
this is trivial, and the job history is what makes the tool safe, so it is not
a place to economise.

The window between the pre-write read and the PATCH cannot be closed without
compare-and-swap support in the API, which does not exist. We narrow it and
document it.

The critical test - and it is in the required test list - is that a crash
between the PATCH and the local commit, followed by a resume, results in
exactly one write, not two. The uniqueness constraint on
`(job_id, document_id, field_kind, field_key)` is the mechanism.

## Alternatives considered

**Compare against `intended_value` on rollback.** Rejected: server-side
normalisation makes it refuse valid rollbacks, and a rollback that usually
refuses is a rollback nobody trusts.

**Unconditional rollback.** Rejected: it is a destructive bulk write with no
conflict detection.

**Store a hash of the whole document instead of per-field values.** Rejected:
any unrelated change - a tag added by matching, a note - would block the
rollback, and the history would lose the human-readable before/after that makes
the audit trail useful.

**Rely on HTTP `ETag` / `If-Match`.** Attractive, but Paperless does not expose
conditional-request support on document endpoints, so there is nothing to rely
on.
