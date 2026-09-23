# Architecture

This document describes how PaperWrench is put together and, more usefully,
why the boundaries are where they are. Individual decisions are recorded in
[decisions/](decisions/); this is the map that connects them.

## The one-sentence version

PaperWrench is a single container running a FastAPI backend that serves a
React SPA from the same origin, talks to an existing Paperless-ngx instance
over its REST API, and uses local SQLite for its own working state. M5 manual edits return a
receipt without durable history; job persistence and rollback are planned for M8/M9.

## Component map

```
                    Browser
                       |
                       |  same origin, relative URLs only
                       v
+----------------------------------------------------------+
|  PaperWrench container (single process, single worker)     |
|                                                            |
|   FastAPI                                                  |
|     /                -> SPA (static assets + index.html)   |
|     /api/v1/...      -> JSON API                           |
|     /api/v1/jobs/../events -> SSE progress stream          |
|                                                            |
|   Domain services                                          |
|     filter engine     - FilterSet -> validate -> compile   |
|                         -> Paperless query params, or a    |
|                         structured refusal. Never a        |
|                         local fallback.                    |
|     transformations   - preview and value computation      |
|     job engine        - asyncio, bounded concurrency       |
|                                                            |
|   Paperless client (httpx.AsyncClient)                     |
|                                                            |
|   SQLAlchemy 2.0 + Alembic                                 |
+-----------------|---------------------------|--------------+
                  |                           |
                  | REST only                 | local file
                  v                           v
        Paperless-ngx (yours)          SQLite /data (WAL)
        SOURCE OF TRUTH                PaperWrench state only
```

Two boundaries matter more than anything else in this diagram.

The **left boundary** is the Paperless REST API, and it is the only way
PaperWrench reaches your documents. No database connection, no filesystem
access, no exceptions (ADR-0002).

The **right boundary** is PaperWrench's own SQLite database, which contains
*only* PaperWrench's working state. It holds no copy of your documents. Deleting
it loses your job history and your ability to roll back; it does not touch your
library.

## Layers

The backend is organised so that the dangerous parts are small and isolated.

**`api/v1/`** — HTTP concerns only: request and response models, status codes,
dependency wiring. No business logic, so that a route handler is never the
place a data-safety rule lives.

**`filters/`** — the Filter Engine (M4). The domain model, the field
catalogue, validation and the compiler. Pure once the catalogue is built:
only `filters/service.py` touches the Metadata Registry, so a filter can be
validated, compiled or refused without a single request leaving the process.

**`services/`** — the rest of the domain. Transformation preview and the job
engine (M6+). This layer is where the rules from the ADRs are implemented and
where the tests that matter point.

**`paperless/`** — the client. One place that knows how to talk to Paperless:
version negotiation, pagination, error mapping, and the read-modify-write
helper for custom fields. Because it is the only door, a hazard fixed here is
fixed everywhere.

Since M2, this package also owns the **normalized model layer** and the
**Metadata Registry**:

- `models.py` defines every PaperWrench-facing shape derived from a Paperless
  response — `Document`, `CustomField`, `CustomFieldValue`, `Tag`,
  `Correspondent`, `DocumentType`, `StoragePath` — plus the typed-value layer
  (`TypedCustomFieldValue`, `CustomFieldValueKind`, `MonetaryAmount`) that
  keeps *absent* / *null* / `""` / `0` / `False` genuinely distinct instead of
  collapsing them, and keeps a select field's stored option id separate from
  its display label. `field_kind()` classifies a document field name as
  core (`CORE_DOCUMENT_FIELDS`) or custom — the distinction the Filter Engine
  (M4) and Transformation Engine (M6) will build on.
- `registry.py` defines `MetadataRegistry`: a small in-memory, per-kind TTL
  cache over the five reference kinds (tags, correspondents, document types,
  storage paths, custom fields) with `by_id` / `by_name` lookups,
  `refresh()` / `invalidate()`, and an explicit `AmbiguousMetadataName` error
  instead of ever silently picking one match. The cache is not a second
  source of truth — Paperless remains authoritative, and there is no SQLite
  mirror of it.

`api/v1/metadata.py` exposes these five reference kinds read-only
(`/api/v1/metadata/tags`, `.../correspondents`, `.../document-types`,
`.../storage-paths`, `.../custom-fields`) as PaperWrench models. This is a
data-layer inspection surface only — it is not the Explorer API and carries
none of its concepts (filters, saved views, bulk operations), which belong to
M3/M4.

### The Explorer's documents API (M3, rebuilt on the Filter Engine in M4)

`api/v1/documents.py` is the first real consumer of the Metadata Registry
as an app-lifecycle singleton rather than a short-lived per-request client:
`main.py`'s `lifespan` now constructs one `PaperlessClient` and one
`MetadataRegistry` and stores them on `app.state` for the life of the
process, and `api/deps.py` hands them out as FastAPI dependencies. This is
the decision `metadata.py`'s M2 docstring explicitly deferred ("out of
scope for M2, better justified once a real consumer needs it") — the
Explorer's document list, which must resolve tags/correspondents/document
types/custom fields on every page without a fresh registry warm-up per
request, is that consumer.

`POST /api/v1/documents/query` is a normalized read model — a list of
`DocumentListItem` inside a `DocumentPage` envelope
(`{items, page, page_size, total, page_count}`), never a passthrough of
Paperless's own `DocumentSerializer` or its `{count, next, previous,
results}` shape (the latter's `next`/`previous` are absolute URLs built
from Paperless's own idea of its hostname — unusable behind a reverse
proxy, per M1). Every reference field (`correspondent`, `document_type`,
`tags`) is already resolved to a display name via the registry, or
represented as an explicit unresolved reference (`MetadataRef(id, name=
None)`, which the Explorer renders as `Unknown (#id)`) rather than ever
being silently coerced to `null` — a real reference and "no reference set"
must stay distinguishable. Custom fields reuse `Document.
typed_custom_fields()` unchanged, so the ABSENT/NULL/PRESENT distinction
and Decimal-safe monetary amounts established in M2 flow straight through
to the grid with no parallel resolution logic.

Three parameters are validated **before** anything is sent to Paperless,
never passed through blindly:

- **`page_size`** must be one of `{25, 50, 100, 250}` — deliberately finite,
  deliberately with no "ALL" option, so the point of a paginated grid
  cannot be defeated by a single query parameter.
- **`ordering`** must be on a server-defined allowlist (see
  `docs/paperless-api.md` §6.2) — core fields (`title`, `created`,
  `modified`, `added`, `archive_serial_number`, `correspondent`,
  `document_type`) plus `custom_field_<id>` for custom fields whose data
  type is Text/Long text, Monetary or Date **and** whose id is actually
  known to the registry. This exists because Paperless silently *ignores*
  an ordering value it does not recognise instead of rejecting it (M1
  finding) — a typo would otherwise look like sorting worked while quietly
  not sorting at all. An unrecognised value is rejected with
  `InvalidOrderingError` (422) and is **never forwarded**.
- **`search`** is a `SearchSpec {mode, text}` with exactly one mode
  (`title` → `title_search`, `content` → `text`, `advanced` → `query`),
  mirroring Paperless's own rule that its four search parameters are
  mutually exclusive (`_TANTIVY_SEARCH_PARAM_NAMES`). M3 had a single
  `search` parameter that always meant *title* search and said so nowhere;
  naming the mode is the point. `advanced` is passed through opaquely —
  PaperWrench never parses Tantivy syntax. An all-whitespace search text is
  **refused**, not dropped: sending `title_search=` puts Paperless into
  search mode with an empty query, a different code path from not
  searching, and dropping it silently would make an empty search box mean
  "everything" without saying so.
- **`filters`** is a `FilterSet`, validated and compiled by the Filter
  Engine before a request is built (below). There is no other filtering
  path.

M3's `document_type`, `correspondent` and `tag` parameters were removed in
M4. Keeping them alongside the Filter Engine would have left two filtering
paths with different capabilities and different failure modes — they would
have disagreed the first time one of them had an opinion about "no value".

### The Filter Engine (M4)

`paperwrench/filters/` is a **domain primitive, not an Explorer feature**.
The same `FilterSet` is what Transform (M6), Dry Run (M7), Jobs (M8),
Quality (M11), Schemas (M10), Collections (M12), Analytics, Exports and
Recipes are all meant to mean by "these documents".

The pipeline is always the same, and always in this order:

```
FilterSet → validation → compiler → PaperlessQuery
```

with a hard stop at either step and **no fallback behind it** (ADR-0007).

**The domain model** (`filters/model.py`) is independent of Paperless HTTP
syntax: `FilterSet → FilterGroup → FilterCondition | FilterGroup |
FilterNot`, discriminated on `kind`. A `FieldRef` is a discriminated union —
a core field is a closed enum, a custom field is referenced by its **stable
Paperless id**. `display_name` exists so a stored filter renders without a
metadata round-trip; it is never matched against, so renaming a custom
field cannot change what a saved filter means.

**Two verdicts, deliberately separate.** *Structurally valid* (the fields
exist, the operators belong to their type, the values have the right shape)
and *compilable* (Paperless can express this exact question) are different
answers that call for different things from the user, so
`POST /api/v1/filters/validate` returns both. `valid: true, compilable:
false` is a common and useful state — the filter is fine, the server simply
has no form for it.

**The compilable subset.** Core conditions AND together as query
parameters; custom-field conditions compile into the single nested
`custom_field_query` expression; the two intersect. An OR whose branches are
all custom fields is supported at any depth. Refused, with a structured
reason and **zero requests**: OR across core fields, OR mixing core and
custom, `NOT`, two conditions collapsing onto one query parameter with
different values, and anything beyond Paperless's own depth-10 / 20-atom
limits. See `docs/paperless-api.md` §6.4–6.5 for the provenance of every
mapping.

**Empty and missing are five states, not two** — `is_missing`,
`is_present`, `is_null`, `has_value`, `is_empty`, each with one server-side
expression, verified live before being frozen (ADR-0011). `is_empty` is
null-or-empty-string and deliberately **not** ABSENT.

**The catalogue is served, not duplicated.**
`GET /api/v1/filters/capabilities` returns the compiler's own tables —
fields, types, allowed operators per type, value shapes, grouping rules —
so the Filter Builder *renders* the rules instead of reimplementing them.
Two copies of a capability matrix drift apart at the first change.

**Counting is not fetching.** `POST /api/v1/filters/count` compiles the
filter and reads Paperless's own `count` from the paginated envelope
(`PaperlessClient.count_documents`, which never parses the results array).
The cost is one request regardless of whether the filter matches twelve
documents or fifty thousand.

The engine is **pure after the catalogue is built**: `validate_filterset`
and `compile_filterset` take a `FieldCatalog` snapshot and do no I/O at all.
That is what makes the compiler exhaustively unit-testable and makes "a
refused filter costs zero requests to Paperless" a property of the code
rather than a promise about its callers.

### Dataset identity

A dataset is `SearchSpec + FilterSet + Ordering` (`DatasetQuery`, with a
stable fingerprint). Pagination describes a *view* of a dataset, not a
different one, and is therefore not part of its identity.

Nothing in M4 persists a dataset. The type exists so the API shipped now can
already express one: Transform, Dry Run, "select all matching", Jobs and
Collections all need to name "the documents this operation is about", and
would otherwise each invent their own encoding and then disagree.

Search is **not** folded into the FilterSet — see **ADR-0010**. It runs
against a Tantivy index whose ids are intersected with the ORM queryset,
which is a fundamentally different mechanism from a field lookup, and
`advanced` carries a query language PaperWrench does not own. Forcing it
into a `FilterCondition` would have made the model look uniform while making
it less truthful.

**`db/`** — models, engine, session, migrations, runtime lock. Owns durability.

**Cross-cutting** — `config.py` (settings, secrets), `logging.py` (structured
logs with secret redaction at the processor level), `errors.py` (the uniform
error envelope).

## Request flow, and where safety is enforced

A bulk transformation goes through five stages. Each stage exists to make the
next one safe.

**1. Select.** The user builds a FilterSet. It is compiled to Paperless query
parameters or rejected with `FILTER_NOT_COMPILABLE` — never partially applied
and never completed client-side (ADR-0007). The document count shown is the
server's count for the same query.

**2. Preview.** The transformation is computed for each matching document and
displayed as before/after. Nothing is written. A `preview_token` binds the
preview to what the user actually saw.

**3. Create.** On confirmation, a `Job` is created and the matching document
ids are **materialised into the job row**. The job never re-evaluates its
FilterSet during execution: a filter is a moving target, and a job whose scope
shifts underneath it cannot be previewed, resumed or rolled back honestly
(ADR-0003).

**4. Execute.** For each document, at bounded concurrency:

- re-read the document;
- compare the current value with `before_value` — if it differs, record
  `SKIPPED_CONFLICT` and move on, do not overwrite;
- for custom fields, merge into the *complete* existing collection (ADR-0004);
- PATCH;
- record what Paperless actually stored as `written_value` (ADR-0005);
- commit the `JobOperation` row before starting the next document.

The `preview_token` and the per-document revalidation are complementary, not
alternatives. The token guarantees the user confirmed the preview they saw; the
revalidation guarantees the document has not moved since. Both are enforced.

**5. Report and undo.** The job ends `COMPLETED`, `PARTIAL` or `FAILED`, with a
per-document breakdown. A rollback is a new job linked by `rollback_of_job_id`
that restores `before_value` only where the current value still equals
`written_value`.

## Data model

The interesting parts:

**`Job`** — type, status, title, the materialised `document_ids_json`, the
originating filter (for explanation), counts, `heartbeat_at`, and
`rollback_of_job_id` when it is a rollback.

**`JobOperation`** — one row per document per field, carrying `before_value`,
`intended_value`, `written_value`, a snapshot of the document's custom fields
before the write, and a status. A unique constraint on
`(job_id, document_id, field_kind, field_key)` makes resumption idempotent at
the storage layer rather than by convention.

**`RuntimeLock`** — a single row (`CHECK (id = 1)`) holding the instance id and
heartbeat that enforce single-instance execution.

**`Schema`, `Collection`, `CollectionDocument`, `AppSettings`** — supporting
state. `AppSettings` deliberately has **no token column**: the Paperless token
comes from the environment and is never persisted.

What is deliberately *not* persisted: document content, OCR text, thumbnails,
any cached copy of Paperless data that could drift, and the API token.

## Job status model

```
PENDING -> RUNNING -> COMPLETED | PARTIAL | FAILED | CANCELLED
              |
              +-----> INTERRUPTED  (process died mid-job)
```

`INTERRUPTED` is not terminal — it is the resumable state. Jobs are never
resumed automatically at startup: an unattended automatic resume of a
destructive write is not a decision software should make. The operator decides.

## Errors

Every error, including routing 404s and validation failures, uses one envelope:

```json
{ "error": { "code": "FILTER_NOT_COMPILABLE", "message": "...", "details": {} } }
```

The frontend therefore needs exactly one error parser. Codes are a closed enum
so the UI can react to specific conditions (`PREVIEW_STALE`, `JOB_CONFLICT`,
`PAPERLESS_INCOMPATIBLE`) rather than pattern-matching on prose.

Unhandled exceptions return a generic `INTERNAL_ERROR`: internal detail could
include a Paperless URL or a header, and none of it belongs in a browser.

## M5 Inspector and document mutations

`api/v1/inspector.py` exposes normalized GET/PATCH `/api/v1/documents/{id}`.
It reuses the Explorer mapper, Document/TypedCustomFieldValue/MonetaryAmount and
MetadataRegistry. `inspector.py` validates the closed edit DTO and refreshes
relevant metadata before validating references/options. A catalogue revision
rejects stale custom-field definitions. Unknown definitions remain visible,
read-only, and preserved in complete writes.

Core allowlist: title, correspondent, document type, storage path, tags, created
(date), archive serial number. Custom allowlist: string, longtext, monetary,
select, date, boolean and integer. URL, float and document-link values remain
read-only; no taxonomy definitions or ownership/permissions can be edited.

The lifecycle client owns `DocumentMutationCoordinator`. Its per-document lock
covers GET → permission/revision check → complete merge → one PATCH → result
capture. Both low-level write helpers share this lock; later Jobs must use the
same lifecycle client/coordinator. `update_document` is strictly core-only;
`mutate_document` is the combined, preconditioned path. There is no public
complete-array passthrough. The API accepts custom operations with explicit
`absent`, `null` or `present` state, never raw upstream `custom_fields` arrays.

The full normalized-document revision deliberately rejects even unrelated
changes. No automatic retry follows 409 or an uncertain network outcome. Only
`user_can_change is True` permits a mutation; 401/403 error codes remain distinct,
and a document 404 can mean invisible. Existing upstream 401/403 errors retain
their common-envelope HTTP 502 mapping; local edit preflight returns HTTP 403.

**VERIFIED_LIVE on 3.1.2:** local serialization prevents cooperating lost updates;
external interleaving still loses an update. Impossible `If-Match` and old
`If-Unmodified-Since` headers do not prevent PATCH. The client therefore requires
explicit external-race acknowledgement for custom writes, and the UI starts each
save with that acknowledgement unchecked. Pausing other writers remains ASSUMED.
A distributed lock would not make the Paperless UI cooperate and is not adopted.
See [ADR-0012](decisions/0012-inspector-coordinated-writes-and-external-race.md).

Before/intended/actual values are returned immediately, not persisted. The UI
shows actual normalization, invalidates document/grid/count caches, and requires
a fresh read before the next edit. M5 has no durable history, crash recovery,
exactly-once delivery or rollback. Timeout/disconnect can leave an unknown result;
never infer that no write happened. M8/M9 remain future milestones.

## Security posture

PaperWrench has no authentication of its own and is designed for a trusted
network. See the threat model in the README before deploying it.

The properties it does guarantee:

- The token lives in the backend process only — never in the database, never in
  a log (redaction is a structlog processor, so an ad-hoc log call cannot leak
  it), never in a response body.
- Same-origin by construction; CORS is empty by default and an origin guard
  rejects cross-origin state-changing requests regardless.
- Security headers (`X-Content-Type-Options`, `X-Frame-Options`,
  `Referrer-Policy`) on every response.
- The container runs as a non-root user with a read-only root filesystem and
  `/data` as the only writable volume.
- No telemetry, no third-party calls.

## Technology choices

| Layer | Choice | Why |
| --- | --- | --- |
| Backend | FastAPI, Python 3.11 | Async HTTP client, typed models, OpenAPI for free |
| Validation | Pydantic v2 | Same models validate input and generate the schema |
| HTTP client | httpx (async) | Bounded-concurrency fan-out with real timeouts |
| ORM | SQLAlchemy 2.0 typed | `mypy --strict` catches model misuse |
| Migrations | Alembic | Schema changes are reviewable; CI checks for drift |
| Database | SQLite (WAL) | No second service; durability is what we need (ADR-0006) |
| Frontend | Vite + React + TS | SPA without a second runtime (ADR-0001) |
| Tables | TanStack Table | Preview grids are large and need virtualisation |
| Server state | TanStack Query | Caching and invalidation, not hand-rolled |
| Styling | Tailwind + shadcn/ui | Owned components, no runtime theme dependency |
| Progress | SSE | One-directional; WebSocket would add protocol for nothing |

## Testing strategy

Tests are weighted towards the write path, because that is where mistakes are
irreversible.

- **Unit** — filter compilation, template rendering, value comparison, the
  custom-field merge, secret redaction, the runtime lock.
- **API** — endpoint contracts against a mocked Paperless (`respx`), including
  the error envelope and the SPA/API routing split.
- **Integration** — against the disposable Paperless-ngx 3.1.2 sandbox, marked
  `live` and never run against a real library.

The non-negotiable cases: custom fields preserved after a single-field write;
zero writes during a Dry Run; forward conflict detection; rollback conflict
detection; a crash between the PATCH and the local commit followed by a resume
producing exactly one write; concurrency actually bounded; and no token in any
log line.
