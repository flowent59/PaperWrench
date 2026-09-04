# ADR-0004: Safe custom-field read-modify-write

## Status

Accepted (M0). This is the most important safety decision in the project.

## Context

Paperless-ngx serialises documents with `DocumentSerializer`, which inherits
from `drf_writable_nested.NestedUpdateMixin`. That mixin implements nested
writes by treating the submitted collection as the desired final state: for a
nested relation, any related object present in the database but absent from
the payload is **deleted**.

Applied to `custom_fields`, this means a PATCH like:

```json
{ "custom_fields": [ { "field": 2, "value": "EUR450.00" } ] }
```

does not set field 2 and leave the rest alone. It sets field 2 and **deletes
every other `CustomFieldInstance` on that document**.

This behaviour was verified in the Paperless-ngx 3.1.2 source (the version this
project targets), not inferred. It is not a bug in Paperless - it is
`drf_writable_nested` behaving exactly as documented - but it is a loaded gun
pointed at any tool that writes custom fields.

The consequences for a bulk tool are severe and, critically, *quiet*. A
transformation that sets `Montant` on 300 documents would silently destroy
every other custom field on all 300. There is no error, no warning, and no
partial failure. The user discovers it later, and the data is gone: Paperless
does not version custom field instances.

The reference dataset makes this concrete. A seeded document carries `Période
concernée`, `Montant`, `Référence interne`, `Établissement`, `Validé`, `Date de
règlement` and often `Commentaire`. A naive write of `Montant` destroys six
fields to change one.

## Decision

PaperWrench never sends a partial `custom_fields` array. Every custom-field
write follows a strict read-modify-write cycle:

1. **Read** the document immediately before writing and take its complete
   current `custom_fields` collection.
2. **Modify** an in-memory copy, merging by field id: replace the entry being
   changed, add it if absent, and leave every other entry byte-identical.
3. **Write** the resulting *complete* collection in the PATCH payload.

Supporting rules, all of which exist because of a specific way this can go
wrong:

- **The read is per-write, not per-job.** The collection is re-read
  immediately before each PATCH, not captured once at preview time. Anything
  else reintroduces the deletion hazard for fields changed between preview and
  execution.
- **Field ids are normalised to integers before merging.** Paperless has
  returned ids as both integers and strings across versions; a merge keyed on
  mixed types silently produces duplicate entries and loses one of them.
- **The pre-write collection is snapshotted** into
  `JobOperation.before_custom_fields_json`. If preservation ever fails despite
  everything, the history contains what was there, which is the difference
  between a recoverable incident and permanent loss.
- **The merge never mutates its inputs.** A shared mutable list is how a
  concurrent job corrupts a payload it does not own.
- **Untouched values are copied verbatim.** No reformatting, no re-parsing, no
  normalisation. In particular monetary values keep their ISO-4217 prefix
  (`EUR450.00`), select values keep the option *id* rather than its label, and
  dates keep their exact serialised form.
- **The rule applies to the seed scripts too.** `seed_dev_custom_field_values.py`
  uses the same merge and the merge itself is unit-tested
  (`test_seed_merge.py`), so the reference implementation cannot silently rot.

An acceptance test on the disposable dev instance asserts that after a
transformation touching one custom field, every other custom field on every
affected document is unchanged - value by value, not merely present.

## Consequences

Every custom-field write costs an extra GET. That doubles the request count of
a custom-field job, and it is not negotiable. The read is needed anyway for
forward conflict detection (ADR-0003), so the two requirements share the cost.

There remains a narrow window between the read and the PATCH in which another
client could add a custom field, which our payload would then delete. This is
inherent to an API with no compare-and-swap and cannot be closed from the
outside. We narrow it by reading as late as possible and document it honestly
rather than pretending it does not exist.

Any future write path that touches documents - `bulk_edit`, a batch endpoint, a
new transformation type - must satisfy this ADR before it ships. It is the
standing rule for the write path, not a one-off fix.

## Alternatives considered

**Send only the changed field.** This is the naive implementation, and it is
the data-loss bug. Rejected.

**Use a dedicated custom-field endpoint.** There is no per-instance endpoint in
the API that avoids the document serialiser.

**Use `bulk_edit`'s `modify_custom_fields`.** It exists, but it is
asynchronous, returns no per-document result and applies a single value to all
documents - so it fails ADR-0003's requirements independently of this one. Its
merge semantics would also need to be verified against 3.1.2 before being
trusted.

**Cache each document's custom fields at preview time and reuse them at write
time.** Cheaper by one request, but it writes a stale collection, which is the
same data-loss bug with a longer window. Rejected.
