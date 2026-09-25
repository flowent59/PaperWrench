# ADR-0013: Expiring preview staging and workflow confirmation

## Status

Accepted for M7. Qualifies the preview portion of ADR-0003. Job creation and
execution remain M8; this record implements neither.

The handoff is now implemented by [ADR-0014](0014-durable-jobs-and-write-provenance.md).
New preview results also bind document/catalogue revisions. The historical
review-only route retains its original non-executing semantics.

## Context

Issue #10 needs exact, paginated before/intended results for explicit IDs or a
compiled DatasetQuery, including errors. Retaining every Document in memory
would scale with the library and keep unnecessary document data. Re-querying
Paperless when the user changes preview pages would show a different preview.
A confirmation must identify the results actually produced, without implying
that those documents still have the same values.

## Decision

Stage one result row per document in SQLite, separate from dormant Job tables.
`previews` contains expiry, readiness, one-time confirmation state, a hash of a
random token and a compact summary. `preview_documents` contains only the ID,
position, status and M6 result JSON, including title and affected values. No OCR,
file, full-document copy, execution value, Job or durable target snapshot is
created. The rows are historical preview evidence, never current source data.

Read Paperless sequentially in pages of 100; stage each batch in a short
transaction before requesting the next page. No transaction or document lock
spans a Paperless await. A unique `(preview_id, document_id)` constraint detects
duplicate targets without an in-memory ID set. Hash sorted IDs through a
streaming database cursor. API pages are bounded to 25/50/100/250 documents;
the UI uses 25 and server-side status filtering.

M7 limits are 100,000 targets, 128 MiB of serialized result data, 300 seconds
per build, one active builder and four retained previews. These are explicit
guardrails, not tested capacity guarantees. Exceeding any limit fails the whole
preview and removes its staging; no truncated success/token is returned.
SQLite indexes, pages and journal add disk overhead beyond the JSON byte limit.
RAM is proportional to one upstream page, its results, the input spec/explicit
ID list and the metadata catalogue, rather than the entire document library.

Expiry is 30 minutes from build start. Cleanup runs at startup, on creation and
on the runtime heartbeat. Startup removes interrupted builds; completed previews
can survive restart until expiry. Deleting a preview cascades its rows. SQLite
reuses freed pages; this is logical deletion, not secure erasure or automatic
file shrinking. Operators should treat the local database as sensitive data.

### Fingerprints and token

SHA-256 fingerprints use canonical UTF-8 JSON with sorted keys, compact
separators, literal Unicode and rejection of non-finite numbers. No float
conversion is introduced for money.

- **Selection**: call the existing `DatasetQuery.fingerprint()` unchanged, or
  hash the sorted explicit-ID array. ID input ordering is immaterial; duplicates
  are rejected. Dataset identity excludes traversal and UI pagination.
- **Spec**: hash preview version, source kind, selection fingerprint and the
  ordered serialized M6 operations (including bindings and values).
- **Targets**: hash ascending decimal document IDs, each followed by a newline,
  from the staging table. Missing/invisible explicit IDs remain attempted targets.
- **Results**: hash each canonical staged result followed by a newline, in preview
  order. Covers statuses, issues, title, field references and typed before/intended
  values, including resolved Select labels. It is not a full-document revision.

M4 currently includes supplied display names in its DatasetQuery fingerprint.
M7 preserves that conservative representation identity; it does not claim
semantic equivalence for renamed labels, reordered conditions or equivalent
search strings. Operation display hints are also part of the spec hash.

The token is a random 256-bit opaque capability returned once by creation. Only
its SHA-256 hash is stored. Its server-side record binds all four fingerprints,
preview version and expiry. It contains no Paperless credential, URL or document
values. Confirmation requires the original token, matching spec, target/result
fingerprints, version, explicit acknowledgement, at least one changed document
and zero error documents. A conditional database update consumes it once;
tampering, cross-preview reuse, expiry and replay fail closed. The ordinary
summary/page endpoints never return the token, and preview responses use
`Cache-Control: no-store`.

M7 confirmation records review only. Apply stays disabled and no Apply route
exists. It is not an execution permission that a future version can replay.
For M8, move the token claim and adoption of these exact staged targets into
**one transaction** with durable Job creation. Do not call M7 `confirm()` and
then separately create a Job. Already-confirmed M7 previews require a new
preview. M8 must persist its own per-target records, not depend on expiring rows
or put a huge document array in the dormant `Job.document_ids_json` field.

### Staleness and error semantics

The observed count must stay constant across pages and equal staged row count;
repeated IDs, inconsistent termination, disappearing/malformed pages and empty
intermediate pages fail the preview. These checks cannot detect every concurrent
same-count replacement, reordering or value edit. Paperless pagination is not
an atomic snapshot; the preview describes an observation interval T0.

At T1 a document may change. A future Apply at T2 must re-read and check current
documents, permissions, metadata and relevant source values before writing,
using ADR-0012's mutation boundary and its external-writer limitations. There
is no lock held between T0 and T2. Workflow integrity is not concurrency control.

Transport, authentication, metadata, query compilation and inconsistent page
failures are global: no usable preview/token. Explicit document 403/404/invalid
payloads and M6 operation issues are per-document errors; other rows remain
reviewable. A malformed dataset page is global because its target boundary
cannot be trusted. Document status precedence is ERROR, then CHANGE, then
UNCHANGED. Operation proposals remain visible even on an error document.
Unconfirmed edit permission or a deleted flag adds DOCUMENT_NOT_EDITABLE;
missing/invisible explicit IDs are deliberately indistinguishable.

## Alternatives

- A giant in-memory array: rejected for document retention proportional to N.
- Recomputing each UI page: rejected because the token would name moving results.
- A stateless signed token only: insufficient to retain exact results/targets,
  enforce one-time use and paginate the same preview.
- A complete Job engine/snapshot now: rejected as M8 scope. Expiring normalized
  result staging solves the concrete M7 need with two small independent tables.

## Evidence and limits

VERIFIED_SOURCE by tests: canonical bindings, one-time confirmation, error
precedence, paging, staging cleanup and GET-only transport. Synthetic 10,000
documents/100 pages verify that previously evaluated Document objects are
released and prior batches reach SQLite before the next request.
VERIFIED_LIVE on guarded Paperless 3.1.2: Golden Dataset count/list/preview
agreement, two-item traversal pages, vacation titles, missing-period errors,
zero money, GET-only probe and unchanged normalized documents after preview.
50,000/100,000-document capacity and production timeout/proxy behavior are
NOT_RUN. A stable upstream library during enumeration remains ASSUMED.
