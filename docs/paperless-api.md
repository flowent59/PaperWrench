# Paperless-ngx API: verified behaviour and hazards

The Paperless REST API is PaperWrench's entire contract with your documents
([ADR-0002](decisions/0002-paperless-rest-api-sole-integration-boundary.md)).
This document records what we rely on, how confident we are in each point, and
where the sharp edges are.

**Reference version: Paperless-ngx 3.1.2.** Findings marked *Verified* were
read in that release's source, not inferred from documentation. The full
analysis is in [architecture-review.md](architecture-review.md).

Confidence levels used below:

- **Verified** — read in the 3.1.2 source.
- **Assumed** — consistent with documentation and observed behaviour, not yet
  confirmed in source.
- **To confirm** — must be established experimentally against the sandbox
  before code depends on it.

---

## API versioning

**Verified.** In 3.1.2, `ALLOWED_VERSIONS = ["9", "10"]` and
`DEFAULT_VERSION = "10"`.

PaperWrench negotiates explicitly on every request:

```
Accept: application/json; version=10
```

Relying on the server default would be a latent bug: the default moves between
releases, and response shapes change with it. An explicit pin produces a clean
`406` against a server that cannot serve version 10, rather than a silent change
in payload structure.

Responses carry `X-Api-Version` and `X-Version`. PaperWrench checks the former
at connection time and reports `PAPERLESS_INCOMPATIBLE` on a mismatch, so the
problem is stated once and clearly instead of surfacing as confusing errors
later.

---

## Hazard 1: `custom_fields` on PATCH is a full replacement

**Verified — this is the single most dangerous behaviour PaperWrench works
around.**

`DocumentSerializer` inherits from `drf_writable_nested.NestedUpdateMixin`
(`src/documents/serialisers.py`, 3.1.2). For nested relations, that mixin
treats the submitted collection as the complete desired state: related objects
present in the database but absent from the payload are **deleted**.

So this request:

```http
PATCH /api/documents/42/
Content-Type: application/json

{ "custom_fields": [ { "field": 2, "value": "EUR450.00" } ] }
```

does not update field 2 and leave the rest alone. It updates field 2 and
**deletes every other `CustomFieldInstance` on document 42**.

There is no error, no warning, no partial failure. On a bulk operation across
300 documents it destroys data on all 300, silently, and Paperless does not
version custom field instances — the values are simply gone.

**What PaperWrench does:** never sends a partial `custom_fields` array. Every
write reads the document immediately beforehand, merges into the complete
existing collection (by integer field id), and sends the whole thing back. The
pre-write collection is snapshotted into the job history. See
[ADR-0004](decisions/0004-safe-custom-field-read-modify-write.md).

**If you are writing code that touches `custom_fields`,** this is the rule you
must satisfy before it ships.

---

## Hazard 2: `bulk_edit` cannot do what a bulk tool needs

**Verified.**

`POST /api/documents/bulk_edit/` looks like the natural endpoint for this
project. It is not usable for the MVP write path, for three independent
reasons:

1. **There is no `set_title` method.** The available methods cover tags,
   correspondent, document type, storage path, permissions, custom fields,
   merge, split, rotate and delete. Renaming — the central MVP transformation —
   is not among them.
2. **One value for all documents.** `bulk_edit` applies the same change to every
   id. A template rename computes a *different* title per document from that
   document's own fields.
3. **Asynchronous and opaque.** It returns once the task is queued, with no
   per-document result. Which documents succeeded, which were skipped, and what
   each value was immediately before the write are precisely the facts a
   preview, a conflict report and a rollback are built from.

**What PaperWrench does:** per-document `PATCH`, with bounded concurrency and a
durable per-document record. See
[ADR-0003](decisions/0003-per-document-patch-as-mvp-write-path.md).

`bulk_edit` stays a legitimate post-MVP optimisation for the operations it
genuinely supports where all documents get the same value.

---

## Hazard 3: Paperless does not always store what you send

**Verified for monetary fields; Assumed for the rest.**

Values are normalised server-side:

- **Monetary** custom fields are stored with an ISO-4217 prefix. Send `450` and
  `EUR450.00` comes back.
- **Select** custom fields store the option **id**, not its label.
- **Dates and datetimes** are re-serialised in Paperless's canonical form and
  timezone.
- **Titles** are subject to server-side handling and length limits.

**Consequence:** comparing a document's current value against what we *intended*
to write produces false conflicts on every successfully written document.
PaperWrench therefore records `written_value` from the PATCH **response body**
and compares against that. See
[ADR-0005](decisions/0005-written-value-and-optimistic-conflict-detection.md).

---

## Hazard 4: tag hierarchy side effects

**Assumed — to confirm against the sandbox before M9.**

Tag matching can add or remove tags as a side effect of an unrelated document
change. A tool that diffs whole documents would report spurious conflicts.

**Consequence:** conflict detection is scoped to the specific field being
written, never to the document as a whole.

---

## Filtering

**Verified.** Paperless exposes Django-filter lookups on core fields:

| Group | Lookups |
| --- | --- |
| `CHAR_KWARGS` | `icontains`, `iexact`, `istartswith`, `iendswith` |
| `ID_KWARGS` | `in`, `exact`, `none` |
| `INT_KWARGS` | `exact`, `gt`, `gte`, `lt`, `lte`, `isnull` |
| `DATE_KWARGS` | `year`, `month`, `day`, `date__gt`, `gt`, `date__lt`, `lt` |
| `DATETIME_KWARGS` | as above, with time components |

Custom fields use `custom_field_query`, which supports nested AND/OR/NOT with
**maximum depth 10** and **maximum 20 atoms**.

**Limitation.** Django filter backends combine query parameters with AND. There
is no server-side expression for OR across heterogeneous core fields — "title
contains X OR correspondent is Y" cannot be compiled.

**What PaperWrench does:** defines a compilable subset. A FilterSet either
compiles completely to query parameters or is rejected with
`FILTER_NOT_COMPILABLE`, naming the condition that cannot be expressed. There
is no client-side filtering fallback, because a capped client-side fetch
produces a wrong count — and the count is the number a user checks before
clicking a destructive button. See
[ADR-0007](decisions/0007-filterset-compilable-subset.md).

---

## Pagination

**Verified.** `StandardPagination`: default `page_size` 25, maximum 100000.

PaperWrench uses 100 by default — large enough to keep request counts sane,
small enough not to build enormous responses on a small self-hosted instance.

Always follow the `next` link to exhaustion rather than computing page numbers.
The `count` field is the authoritative total and is what the UI shows before a
destructive operation.

---

## Ordering

**Verified.** Ordering is restricted to a server-side whitelist, which includes
`custom_field_<id>` for sorting by a custom field.

An ordering outside the whitelist is not applied. PaperWrench rejects it rather
than silently dropping it: a preview sorted differently from the executed job
misrepresents which documents "the first 50" are.

---

## Other verified details

- **`document_count`** is exposed on tags, correspondents, document types and
  storage paths — useful for dashboards without listing documents.
- **`duplicate_documents`** exists on the document resource and is the starting
  point for M10 rather than reimplementing detection.
- **`deleted_at`** and the trash mechanism mean a "deleted" document may still
  be returned. Filters must account for it.
- **`user_can_change`** on a document reflects the token's permissions.
  PaperWrench reads it and records `SKIPPED_PERMISSION` instead of attempting a
  write that will fail.

---

## Still to confirm experimentally

These must be established against the sandbox before the code that depends on
them ships:

- Exact tag matching side effects on PATCH (Hazard 4).
- Whether `bulk_edit`'s `modify_custom_fields` merges or replaces — relevant
  only if `bulk_edit` is adopted post-MVP.
- Server-side title normalisation and length limits precisely.
- Behaviour of `custom_field_query` at exactly the documented depth and atom
  limits.
- Rate-limiting or throttling behaviour under sustained concurrent writes.

---

## Upgrading the pinned Paperless version

The sandbox pins 3.1.2 on purpose. Raising it is not a string change:

1. Re-read `ALLOWED_VERSIONS` and `DEFAULT_VERSION` in the new release.
2. Re-check that `DocumentSerializer` still uses `NestedUpdateMixin`, and
   whether the replacement semantics changed.
3. Re-check the filter lookup groups and the ordering whitelist.
4. Run the `live` test suite against the new sandbox.
5. Update this document with what changed.
