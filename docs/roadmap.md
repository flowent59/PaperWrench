# Roadmap

Milestones are ordered so that the dangerous capabilities arrive last, on top
of foundations that make them safe. Nothing writes to Paperless before M9, and
M9 depends on the preview, conflict detection and durable history built before
it.

Status legend: **Done** · **Next** · **Planned**

---

## M0 — Foundations · Done

Repository, tooling, and the skeleton everything else hangs off.

- Backend: FastAPI app factory, settings with secret handling, structured
  logging with token redaction, uniform error envelope
- Database: SQLAlchemy 2.0 models, Alembic, SQLite WAL, single-instance runtime
  lock with heartbeat and stale takeover
- Frontend: Vite + React + TypeScript + Tailwind + shadcn/ui, app shell,
  light/dark, typed API client, centralised UI strings
- `GET /system/health`, `GET /system/info`
- Single-container Dockerfile (non-root), production and development compose
  files, disposable Paperless-ngx 3.1.2 sandbox with a seeded reference dataset
- CI: lint, strict type checking, tests, image build, container smoke test,
  token-leak check
- GPL-3.0, README with threat model, seven ADRs

Nothing in M0 writes to Paperless.

## M1 — Paperless connection · Next

The client, and proving we can talk to a real instance safely.

- `httpx.AsyncClient` with explicit API version negotiation
  (`Accept: application/json; version=10`) and `X-Api-Version` verification
- Error mapping to `PAPERLESS_UNREACHABLE` / `UNAUTHORIZED` / `INCOMPATIBLE`
- Pagination helper following `next` to exhaustion
- `GET /system/paperless` — connection test surfaced in the UI
- Reference data: tags, correspondents, document types, storage paths, custom
  field definitions
- Tests with `respx`, plus `live` tests against the sandbox

## M2 — Document browsing · Planned

- Paginated document list backed by server-side pagination and ordering
- TanStack Table grid with column selection and virtualisation
- Document detail with native PDF preview (`<object>`/`<iframe>`)
- Ordering restricted to the server whitelist, including `custom_field_<id>`

## M3 — FilterSets · Planned

- FilterSet and FilterCondition models, persistence
- Compiler to Paperless query parameters, including `custom_field_query`
- `FILTER_NOT_COMPILABLE` with a specific reason, and no client-side fallback
  ([ADR-0007](decisions/0007-filterset-compilable-subset.md))
- Filter builder UI that shows its limits before submission
- Server-provided result count

## M4 — Transformations and preview · Planned

- Transformation model: template rename, set/clear field, find and replace,
  case transforms
- Template engine over document fields and custom fields, with
  `TEMPLATE_UNRESOLVED` on missing data rather than silent empties
- **Dry Run preview**: per-document before/after, with a test asserting zero
  HTTP writes
- `preview_token` binding a confirmation to the preview it came from

## M5 — Job engine · Planned

- Job and JobOperation persistence, status transitions
- asyncio execution with bounded concurrency and a hard ceiling
- SSE progress stream
- `INTERRUPTED` detection at startup; explicit, never automatic, resume
- Idempotent resumption via the uniqueness constraint

## M6 — Data quality · Planned

- Rules: missing field, malformed date, inconsistent type, orphan tag
- Quality dashboard with drill-down into the offending documents
- One-click construction of a FilterSet from a finding

## M7 — Schemas · Planned

- Per-document-type expected-field definitions
- Conformance validation and reporting
- Suggested corrections, always through the normal preview path

## M8 — Execution and history · Planned

- Job history with the full per-document operation record
- Filterable operation view (written, skipped, conflicted, failed)
- Export of a job report

## M9 — Writes and rollback · Planned

**The first milestone that modifies your library.**

- Per-document PATCH with verify-before-write and verify-after-write
  ([ADR-0003](decisions/0003-per-document-patch-as-mvp-write-path.md))
- Safe custom-field read-modify-write
  ([ADR-0004](decisions/0004-safe-custom-field-read-modify-write.md))
- `written_value` capture and optimistic conflict detection
  ([ADR-0005](decisions/0005-written-value-and-optimistic-conflict-detection.md))
- Rollback as a linked job, refusing per document where the value has moved
- The full critical test set green on the sandbox before any real write

## M10 — Duplicates · Planned

- Surface Paperless's own `duplicate_documents`
- Near-duplicate detection on metadata
- Side-by-side comparison and guided resolution

## M11 — Extraction · Planned

- Regex extraction from OCR content into custom fields
- Pattern library per document type
- Preview before write, like every other transformation

## M12 — Analytics · Planned

- Library statistics: volume over time, distribution by type, correspondent,
  tag
- Custom field coverage and completeness
- Monetary aggregation for monetary fields

## M13 — AI-assisted review · Planned

- Optional, explicitly opt-in, and off by default
- Suggestions only: never an automatic write
- Bring-your-own endpoint; no data leaves the instance unless configured

---

## Not planned

Some things are out of scope by design rather than by priority:

- Document storage, OCR, or a consumption pipeline — that is Paperless's job
- Replacing or forking Paperless-ngx
- Multi-instance or horizontally scaled deployment
  ([ADR-0006](decisions/0006-sqlite-durable-job-engine-single-instance.md))
- Direct Paperless database or filesystem access
  ([ADR-0002](decisions/0002-paperless-rest-api-sole-integration-boundary.md))
- Telemetry of any kind
