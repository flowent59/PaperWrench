# Architecture

This document describes how PaperWrench is put together and, more usefully,
why the boundaries are where they are. Individual decisions are recorded in
[decisions/](decisions/); this is the map that connects them.

## The one-sentence version

PaperWrench is a single container running a FastAPI backend that serves a
React SPA from the same origin, talks to an existing Paperless-ngx instance
over its REST API, and records everything it does in a local SQLite database so
that it can be previewed, resumed and undone.

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
|     filter compiler   - FilterSet -> query params          |
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

**`services/`** — the domain. Filter compilation, transformation preview, the
job engine. This layer is where the rules from the ADRs are implemented and
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
