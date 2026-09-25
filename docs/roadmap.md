# Roadmap

Milestones are ordered so that the dangerous capabilities arrive last, on top
of foundations that make them safe. M5 introduces explicit single-document edits. Bulk job writes remain M8 and
depend on normalized data, filters, transformations and dry-run preview.

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

## M3 — Explorer + server-side DataGrid · Done

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
  PaperWrench. *(Superseded in M4 by an explicit `SearchSpec {mode, text}`:
  M3's `search` always meant title search and documented that nowhere.)*
- Ordering restricted to a **server-defined allowlist**, validated and
  translated before any request reaches Paperless — an unrecognised value
  is rejected with 422, never silently forwarded (Paperless itself silently
  ignores an unknown ordering value, M1 finding). `custom_field_<id>`
  ordering exposed only for Text/Long text, Monetary and Date custom
  fields, and only once VERIFIED_LIVE against the Golden Dataset
- Basic direct filters (document type, correspondent, tag) mapping onto
  `DocumentFilterSet` fields already proven to compose with pagination and
  ordering — explicitly **not** a filter engine: no FilterSet, no
  compiler, no nested AND/OR, no saved filters (that is M4).
  *(Removed in M4 and replaced by the Filter Engine, so there is one
  filtering path rather than two.)*
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

## M4 — Filter Engine · Done

The reusable query primitive the rest of PaperWrench is built on, not a set
of filters bolted onto the Explorer. `FilterSet` is what Transform, Dry Run,
Jobs, Quality, Schemas, Collections, Analytics, Exports and Recipes are all
meant to mean by "these documents".

- **Domain model** (`paperwrench/filters/model.py`) independent of Paperless
  HTTP syntax: `FilterSet → FilterGroup → FilterCondition | FilterGroup |
  FilterNot`, discriminated on `kind`
- **`FieldRef`** as a discriminated union — a core field is a closed enum, a
  custom field is referenced by its **stable Paperless id**. A display name
  is carried for rendering and is never the identity, so renaming a custom
  field cannot change what a saved filter means
- **Strict compiler**: `FilterSet → validation → compiler → PaperlessQuery`,
  with a hard stop at either step and **no local fallback** —
  `FILTER_NOT_COMPILABLE` is never a signal to fetch a wider set and filter
  in Python ([ADR-0007](decisions/0007-filterset-compilable-subset.md)),
  covered by a guard test whose mock Paperless accepts every parameter and
  still must not be reached
- **Compilable subset**: core conditions AND together as query parameters,
  custom-field conditions compile into the single nested `custom_field_query`
  expression, and the two intersect. A custom-field-only OR is supported at
  any depth; OR across core fields, OR mixing core and custom, `NOT`,
  parameter collisions and anything beyond Paperless's depth-10/20-atom
  limits are refused with a structured reason and zero requests
- **Core fields**: title, correspondent, document type, storage path, tags,
  created, added, modified, archive serial number — each with only the
  operators its column actually supports
- **Custom fields** by data type, with the operator matrix taken from
  Paperless's own `SUPPORTED_EXPR_CATEGORIES`: no `contains` on a Boolean,
  no `is_empty` on a Monetary
- **Empty/missing semantics** as five distinct operators — `is_missing`,
  `is_present`, `is_null`, `has_value`, `is_empty` — verified live before
  being frozen ([ADR-0011](decisions/0011-empty-and-missing-are-not-the-same-question.md)).
  `is_empty` is null-or-empty-string and deliberately **not** ABSENT
- **Monetary** stays `Decimal` end to end, normalised to a bare decimal
  string so Paperless's currency-prefix heuristic has nothing to guess at;
  `EUR0.00` is a real, comparable value distinct from ABSENT
- **Select** filters on the stored option id, never the label — Paperless
  would resolve a label, which means a rename would silently retarget a
  saved filter
- **Tags**: `has_all_of` / `has_any_of` / `has_none_of` mapped to the three
  genuinely different Paperless semantics, verified live; no simulated
  set operations
- **`POST /api/v1/filters/validate`** reporting `valid` and `compilable`
  separately, with structured, path-addressed issues
- **`POST /api/v1/filters/count`** using Paperless's own count — no document
  is fetched to produce it
- **`GET /api/v1/filters/capabilities`** serving the compiler's own tables so
  the frontend renders the rules instead of reimplementing them
- **Explorer rebuilt on it**: `POST /api/v1/documents/query` replaces
  `GET /api/v1/documents` and its three ad-hoc filter parameters, so there is
  one filtering path. Body is `search + filters + ordering + page +
  page_size`
- **Search kept separate** from FilterSet as an explicit `SearchSpec
  {mode, text}` ([ADR-0010](decisions/0010-search-is-not-a-filter.md)); a
  dataset is `SearchSpec + FilterSet + Ordering`, with pagination only a view
  of it
- **Filter Builder UI** constrained by the real capabilities: it cannot
  construct a shape the compiler is known to refuse
- Still no fetch-all, still read-only: no PATCH/POST/PUT/DELETE reaches
  Paperless from anything in this milestone

Deferred to a later milestone, deliberately: FilterSet **persistence** (saved
filters — nothing here writes to SQLite), `NOT` compilation, and materialising
a job's target document ids (that belongs with the Job Engine, M8).

## M5 — Inspector + inline editing · Done

- #7: runtime-enforced client write boundary, closed core allowlist, no arbitrary
  custom-field replacement arrays, shared mutation coordinator
- Normalized single-document API and Inspector opened from Explorer titles
- Inline save/cancel for supported core and custom fields; explicit absent/null/
  present, Decimal-safe money and select option IDs
- Before/intended/actual values from the synchronous PATCH response
- Permission gating, stale document/catalogue conflicts and query invalidation
- ADR-0012: single-process per-document serialization; external writers remain
  non-atomic and require explicit risk acknowledgement on custom-field saves
- Guarded 3.1.2 tests for preservation, normalization, permissions, stale state,
  deterministic local and external interleaving, and ignored conditional headers
- No durable edit history or rollback; these remain M8/M9. No M6 work included.

## M6 — Transformation Engine · Done

- Pure per-document SET, CLEAR, literal REPLACE and strict TEMPLATE proposals
  using `FieldRef`, typed Paperless values and explicit target IDs or `DatasetQuery`
- `before`/`intended` plus change/unchanged/error records; ABSENT, NULL,
  empty string, zero and false remain distinct
- Read-only one-document evaluation API and authoring UI; dataset-wide preview,
  confirmation, execution, durable history and rollback remain M7–M9
- Case transforms remain outside the MVP scope defined by issue #9

## M7 — Dry Run · Done

- Per-document before/after preview, with a test asserting zero HTTP writes
- `preview_token` binding a confirmation to the exact preview it came from
- Strict distinction preserved end-to-end: `before_value` (what Paperless
  had), `intended_value` (what the transformation wants) — `written_value`
  does not exist yet at this stage and must never be guessed from
  `intended_value`
- Explicit IDs and the existing compiled DatasetQuery, with no local fallback
- Sequential server pages, expiring SQLite result staging, paginated UI/API
  rows and distinct changed/unchanged/error counts; bounded memory and explicit
  document/byte/time/capacity limits (ADR-0013)
- Token binds spec, selection identity, actual target IDs, result fingerprint,
  version and expiry; confirmation is one-time local review, never execution
- Explorer selection/dataset handoff, error review, stale/expired gating and
  disabled Apply; no Job creation, durable target snapshot or history
- Guarded 3.1.2 Golden Dataset read-only probe and synthetic 10k/100-page
  retention test; 50k/100k live capacity remains NOT_RUN

## M8 — Job Engine + History · Done

**Bulk execution and durable job history; manual edits already exist in M5.**

- Job/Target/Operation persistence and migration away from monolithic target JSON
- Transactional confirmation, Job creation and exact target adoption
- Fixed asyncio worker pool and shared mutation semaphore, default 4, ceiling 16
- Durable progress with browser polling and server pagination (ADR-0014 supersedes SSE)
- Per-document PATCH with verify-before-write and verify-after-write
  ([ADR-0003](decisions/0003-per-document-patch-as-mvp-write-path.md))
- Safe custom-field read-modify-write, non-bypassable by normal
  `PaperlessClient` consumers
  ([ADR-0004](decisions/0004-safe-custom-field-read-modify-write.md))
- `written_value` capture and optimistic conflict detection, complementary to
  `preview_token`
  ([ADR-0005](decisions/0005-written-value-and-optimistic-conflict-detection.md))
- `INTERRUPTED` detection at startup; explicit, never automatic, resume
- Safe resume of unsent targets; send uncertainty remains ambiguous/manual review
- Job history with the full per-document operation record
- Filterable target view and paginated field audit (before/intended/actual written)
- No automatic write retry, rollback execution or user cancellation
- The full critical test set green on the sandbox before any real write

## M9 — Rollback · Done

- Rollback as a linked job, built entirely on M8's per-document operation
  history
- Refuses per document where the value has moved since the original write
- Same expiring, single-use preview-before-confirm discipline as any other Job
- Shared M8 execution/recovery; original History remains immutable
- One rollback per original; grouped conflicts and ambiguous writes fail closed
- VERIFIED_LIVE on disposable 3.1.2: normalized title/money restored, later custom
  neighbor preserved, later title and deleted document refused
- Contract and evidence: [ADR-0015](decisions/0015-safe-rollback-jobs.md)

## M10 — Schemas · Done

- Named schema CRUD with a compilable `DatasetQuery` applicability scope
- Typed `required` and `equals` rules over stable field IDs
- Read-only, bounded paginated conformance and a focused editor
- Document-level result contract for M11; see [schema API](schema-api.md)

## M11 — Quality · Done

- Read-only summary and paginated violations from M10 `required`/`equals` rules
- Separate evaluated-document, violation and exact dataset counts
- Exact compiled `FilterSet` drill-down where available; bounded explicit IDs
  otherwise, with stable Explorer links
- Distinct absent, null, empty, zero, false, Decimal and Select ID results
- Contract and limits: [quality API](quality-api.md)

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
