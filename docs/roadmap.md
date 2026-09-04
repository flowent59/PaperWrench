# Roadmap

Milestones are ordered so that the dangerous capabilities arrive last, on top
of foundations that make them safe. Nothing writes to Paperless before M8, and
M8 depends on the normalized data layer, filters, transformations and dry-run
preview built before it.

This numbering is **canonical and fixed**: milestone numbers are never
reassigned to different content once a milestone starts. If scope needs to
move between milestones, the content moves — the number of the milestone that
owns "Explorer", "Filter Engine", "Job Engine" or "Rollback" does not.

Status legend: **Done** · **Next** · **Planned**

---

## M0 — Repository + Docker + frontend/backend · Done

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
- GPL-3.0, README with threat model, ADRs

Nothing in M0 writes to Paperless.

## M1 — Paperless connection + compatibility · Done

The client, and proving we can talk to a real instance safely.

- `httpx.AsyncClient` with explicit API version negotiation
  (`Accept: application/json; version=10`)
- Compatibility decided by HTTP status (406), never by echoing
  `X-Api-Version` back as proof of negotiation
  ([ADR-0008](decisions/0008-api-compatibility-is-decided-by-status-code.md))
- Error mapping: `PAPERLESS_UNREACHABLE` / `UNAUTHORIZED` / `INCOMPATIBLE` /
  `NOT_FOUND` / `VALIDATION_ERROR` / `CONFLICT`
- `GET /system/paperless` — connection test surfaced in the UI
- `custom_fields` PATCH confirmed destructive on partial payloads; safe
  read-modify-write helper (`merge_custom_fields`) as the only path
- Golden Dataset seeder for the sandbox, idempotent and title-trim-aware
- Tests with `respx`, plus `live` tests against the real 3.1.2 sandbox

## M2 — PaperlessClient + normalized models · Done

Finishing the data boundary: nothing downstream of this milestone touches raw
Paperless JSON.

- Normalized models: `Document`, `CustomField`, `CustomFieldValue`, `Tag`,
  `Correspondent`, `DocumentType`, `StoragePath`, `Page[T]`, with an explicit
  distinction between core fields and custom fields
- `PaperlessClient` primitives to fetch and normalize tags, correspondents,
  document types, storage paths, alongside the existing custom-field
  definitions
- Metadata Registry: id → metadata and name → id lookups for every metadata
  type, with explicit, non-silent handling of ambiguous (non-unique) names
- Small in-memory TTL cache for metadata (refresh / invalidate / expire);
  the cache is never the source of truth and there is no SQLite mirror of
  Paperless metadata
- Typed custom-field values that keep Text / Monetary / Select / Date /
  Integer / Float / Boolean / `null` / `""` / absent genuinely distinct —
  nothing collapses into a generic "empty"; monetary values never pass
  through `float`
- Select fields expose both the stored option id and the resolvable display
  label, without ever substituting one for the other
- HTTP 409 confirmed **non-retryable by default**
  (`paperwrench.paperless.errors.PaperlessConflictError`)
- Page-number pagination (not blind `next`-following), with clean handling
  of the out-of-range "Invalid page." 404
- Internal metadata API exposing PaperWrench models (not raw Paperless
  serializer copies) — no Explorer API yet

## M3 — Explorer + server-side DataGrid · Next

Read-only. Turns the normalized model layer into a usable way to browse a
real library without ever loading it whole into the browser.

- `GET /api/v1/documents`: a normalized, paginated document list
  (`DocumentPage`/`DocumentListItem`), never a passthrough of Paperless's
  `DocumentSerializer` or `{count, next, previous, results}` envelope
- Server-side pagination only — no fetch-all, ever; `page_size` restricted
  to `{25, 50, 100, 250}`, no "ALL" option
- Simple search (`search` → `title_search`) and an optional opaque `query`
  passthrough, mutually exclusive, mirroring Paperless's own
  `_TANTIVY_SEARCH_PARAM_NAMES` rule — no Tantivy syntax parsing in
  PaperWrench
- Ordering restricted to a **server-defined allowlist**, validated and
  translated before any request reaches Paperless — an unrecognised value
  is rejected with 422, never silently forwarded (Paperless itself silently
  ignores an unknown ordering value, M1 finding). `custom_field_<id>`
  ordering exposed only for Text/Long text, Monetary and Date custom
  fields, and only once VERIFIED_LIVE against the Golden Dataset
- Basic direct filters (document type, correspondent, tag) mapping onto
  `DocumentFilterSet` fields already proven to compose with pagination and
  ordering — explicitly **not** a filter engine: no FilterSet, no
  compiler, no nested AND/OR, no saved filters (that is M4)
- TanStack Table grid: server-side pagination and sorting, search, column
  visibility (persisted to `localStorage`), multi-selection with
  select-one/select-several/select-current-page (never "select all N
  matching" — that needs FilterSet materialization and belongs to
  M4/M6/M7), loading/empty/error states
- Dynamic custom-field columns sourced from the M2 Metadata Registry, never
  hardcoded; ABSENT/NULL/PRESENT and Decimal-safe monetary amounts
  preserved through to the grid; select fields keep their stored option id
  distinct from their display label
- Unresolved metadata references render as `Unknown (#id)`, never crash and
  never silently collapse to `null`
- `user_can_change` preserved through to the frontend for later milestones
  (no special UI treatment yet)
- Explorer replaces the disabled "Documents" sidebar entry as the first
  real functional page
- No write path: zero PATCH/POST/PUT/DELETE requests to Paperless's
  documents endpoint from anything in this milestone (verified by a
  dedicated backend test)

## M4 — Filter Engine · Planned

- FilterSet and FilterCondition models, persistence
- Compiler to Paperless query parameters, including `custom_field_query`
- No dynamically generated filter or ordering parameter is ever sent to
  Paperless without being validated by PaperWrench's own allowlist/compiler
  first; a non-compilable filter fails explicitly instead of being sent "to
  see if Paperless accepts it" (Paperless silently ignores unknown filters
  and returns 200, so silent send-and-hope is not an option)
- `FILTER_NOT_COMPILABLE` with a specific reason, and no client-side fallback
  ([ADR-0007](decisions/0007-filterset-compilable-subset.md))
- Filter builder UI that shows its limits before submission
- Server-provided result count
- Job target document sets are materialized into IDs at job creation time;
  a Job never re-evaluates its FilterSet during execution

## M5 — Inspector + inline editing · Planned

- Per-document Inspector view built on the M2 normalized `Document` model
- Inline editing of core fields and custom fields, going exclusively through
  the safe read-modify-write path — no direct partial PATCH from the UI
- Clear separation of `before_value` / `intended_value` in the editing UI,
  ready to receive `written_value` once M8 lands
- Surfaces `user_can_change` so the UI never offers an edit Paperless would
  refuse

## M6 — Transformation Engine · Planned

- Transformation model: template rename, set/clear field, find and replace,
  case transforms
- Template engine over document fields and custom fields, with
  `TEMPLATE_UNRESOLVED` on missing data rather than silent empties
- Transformations are defined and validated here; nothing is written to
  Paperless by this milestone

## M7 — Dry Run · Planned

- Per-document before/after preview, with a test asserting zero HTTP writes
- `preview_token` binding a confirmation to the exact preview it came from
- Strict distinction preserved end-to-end: `before_value` (what Paperless
  had), `intended_value` (what the transformation wants) — `written_value`
  does not exist yet at this stage and must never be guessed from
  `intended_value`

## M8 — Job Engine + History · Planned

**The first milestone that modifies your library.**

- Job and JobOperation persistence, status transitions
- asyncio execution with bounded concurrency and a hard ceiling
- SSE progress stream
- Per-document PATCH with verify-before-write and verify-after-write
  ([ADR-0003](decisions/0003-per-document-patch-as-mvp-write-path.md))
- Safe custom-field read-modify-write, non-bypassable by normal
  `PaperlessClient` consumers
  ([ADR-0004](decisions/0004-safe-custom-field-read-modify-write.md))
- `written_value` capture and optimistic conflict detection, complementary to
  `preview_token`
  ([ADR-0005](decisions/0005-written-value-and-optimistic-conflict-detection.md))
- `INTERRUPTED` detection at startup; explicit, never automatic, resume
- Idempotent resumption via the uniqueness constraint
- Job history with the full per-document operation record
- Filterable operation view (written, skipped, conflicted, failed) and export
  of a job report
- The full critical test set green on the sandbox before any real write

## M9 — Rollback · Planned

- Rollback as a linked job, built entirely on M8's per-document operation
  history
- Refuses per document where the value has moved since the original write
- Same preview-before-confirm discipline as any other job

## M10 — Schema · Planned

- Per-document-type expected-field definitions
- Conformance validation and reporting
- Suggested corrections, always through the normal preview path

## M11 — Quality · Planned

- Rules: missing field, malformed date, inconsistent type, orphan tag
- Never collapses `0` / `false` / `""` / `null` / absent into one "empty"
  notion when evaluating a rule — this is why M2 keeps them distinct
- Quality dashboard with drill-down into the offending documents
- One-click construction of a FilterSet from a finding

## M12 — Collections · Planned

- Grouping of documents (duplicates, near-duplicates, related sets) into
  named collections
- Surface Paperless's own `duplicate_documents` plus near-duplicate detection
  on metadata
- Side-by-side comparison and guided resolution

## M13 — Polish / tests / release · Planned

- Hardening pass across the full stack; closing debt logged by earlier
  milestones
- Full regression of the critical test set, docs and ADRs brought up to date
- Packaging and release readiness

---

## Vision beyond M13 (not scheduled)

PaperWrench's longer-term ambition includes regex/pattern extraction into
custom fields, library analytics (volume, distribution, monetary
aggregation), and an explicitly opt-in, suggestions-only AI-assisted review.
These stay part of the project's stated scope but are deliberately left
unscheduled — they are not assigned a milestone number until M0–M13 above are
delivered, so that adding them later never requires renumbering anything in
this document.

## Not planned

Some things are out of scope by design rather than by priority:

- Document storage, OCR, or a consumption pipeline — that is Paperless's job
- Replacing or forking Paperless-ngx
- Multi-instance or horizontally scaled deployment
  ([ADR-0006](decisions/0006-sqlite-durable-job-engine-single-instance.md))
- Direct Paperless database or filesystem access
  ([ADR-0002](decisions/0002-paperless-rest-api-sole-integration-boundary.md))
- Telemetry of any kind
