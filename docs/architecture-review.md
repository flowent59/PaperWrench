# PaperWrench — Architecture Review (Sections A–L)

**Status:** historical pre-implementation review; retained as design history.

Current behavior is documented in [architecture.md](architecture.md) and accepted
[ADRs](decisions/). In particular ADR-0008 supersedes this review's
X-Api-Version negotiation assumption; ADR-0014/0015 supersede SSE, monolithic
target arrays and exactly-once/provenance assumptions. This is not a current
implementation or release acceptance report.
**Reviewed spec:** PaperWrench Master Development Brief
**Verification base:** Paperless-ngx official docs (`docs.paperless-ngx.com/api/`) + `paperless-ngx@main` source (`src/documents/filters.py`, `serialisers.py`, `views.py`, `models.py`, `src/paperless/views.py`), API version **10**

> **Headline:** the spec is unusually well-reasoned and I recommend building it essentially as written. There are **three blocking findings**, all discovered in the Paperless write path, and all of them are data-safety issues that would have caused silent data loss if we had implemented the MVP from the spec's assumptions. They are described in B.3, C.9 and C.10.

---

## A. Understanding

### What PaperWrench is

PaperWrench is a self-hosted **companion** to Paperless-ngx that operates strictly *downstream* of ingestion. Paperless keeps every responsibility it already has — storage, OCR, consumption, metadata ownership, versioning, audit log — and PaperWrench becomes the **power-tool layer** on top of it: a place to slice a library into datasets, understand what is wrong with them, fix them in bulk, and prove what happened.

Its long-term value is not any single screen. It is one reusable pipeline:

```
FilterSet → Dataset → {Transform | Validate | Aggregate} → Job → Paperless REST API
```

Every feature on the roadmap (Quality, Analytics, Collections, Recipes, CSV round-trip, Series, Extraction) is a different consumer of that same pipeline. If the MVP gets `FilterSet`, `Transformation`, `Job` and `Rollback` right, the roadmap becomes mostly UI work. If it gets them wrong, every later feature inherits the mistake.

### What PaperWrench is not

Not a DMS, not a document store, not an OCR engine, not an ingestion system, not a prettier Paperless frontend, and explicitly **not** a second writer to Paperless' database or media directory. Every read and write goes through the documented REST API. PaperWrench's own SQLite database holds only PaperWrench-native concepts (schemas, collections, jobs, operations, settings) — never a mirror of the Paperless library.

There is a second, subtler "is not" that I want to state explicitly because it will come up repeatedly during implementation: PaperWrench is **not a cache**. The moment we start persisting document metadata to make Explorer faster, we own a staleness problem, an invalidation problem and a conflict problem, and we become a competing source of truth. The MVP must resist this even when it is tempting.

### What the MVP must prove

The MVP is not trying to prove that we can render a table. It must prove four things:

1. **The pipeline abstraction is real.** Explorer, Quality and Collections all consume the same `FilterSet`. If any of them needs its own bespoke query path, the abstraction has failed.
2. **Bulk mutation can be made safe.** Filter → Preview → Confirm → Execute → Report, with an honest dry run computed *before* any write, and errors surfaced rather than swallowed.
3. **Rollback is trustworthy.** Not "we stored the old value and we'll shove it back", but "we stored what we actually wrote, we verify it is still there, and we refuse to clobber somebody else's later edit".
4. **It scales to a real library.** 100k documents, server-side pagination/filtering/sorting, bounded concurrency, no "fetch everything then filter in the browser".

The acceptance scenario in §20 (bulk-renaming `Relevé de vacations` documents from a template with a rollback afterwards) is a good north star precisely because it exercises all four at once.

---

## B. Architecture review

### B.1 Good decisions (keep, do not re-litigate)

**FilterSet as the single query primitive.** This is the most important call in the spec and it is correct. Banning `get_spv_documents()`-style functions early prevents the exact rot that kills tools like this.

**Paperless as sole source of truth, REST-only, no DB/media access.** Non-negotiable and correctly framed. It also happens to be what makes PaperWrench safe to install next to somebody's real archive.

**Dry Run as a mandatory product rule, not a feature flag.** "Preview first, apply second" is the right default for a tool whose blast radius is measured in thousands of documents.

**Job Engine with per-document operation records.** Persisting old *and* new values per document per field is what makes History, partial failure and Rollback possible. Getting this into the MVP rather than bolting it on later is the right sequencing.

**Rollback conflict detection called out in the spec itself.** Most projects discover this in production. The spec's suggestion — verify the current value still equals what the job wrote — is exactly right and I've built on it in D/H.

**No Redis/Celery/RabbitMQ/Elasticsearch in MVP.** Correct. A single-container FastAPI + SQLite deployment is achievable for this workload and dramatically lowers the self-hosting barrier. See H for how to do it without lying about the reliability trade-offs.

**FastAPI + Pydantic + HTTPX async + SQLAlchemy + SQLite.** Well-matched to an API-orchestration workload: the app is almost entirely I/O-bound against Paperless, and async HTTPX with a bounded semaphore is the natural fit.

**§44 architectural foresight rule.** "Make the model capable of nesting; don't build the visual rule builder" is the correct calibration and I've applied it literally in D.

### B.2 Risky decisions and challenges

#### Challenge 1 — Drop Next.js. Use Vite + React SPA. (recommend)

Next.js earns its complexity through SSR/SSG/RSC/routing-driven data loading. PaperWrench gets none of that value:

- Every meaningful screen is authenticated, dynamic and behind a private network — SSR buys nothing.
- The Paperless token must stay server-side, and we already have a Python backend to hold it. Next.js's server half would be a *second* server-side runtime whose only job is proxying to the first one. That's two ways to do everything and a doubled container.
- Node in the production image adds build weight and CVE surface for a self-hosted app.

**Recommendation:** Vite + React 18 + TypeScript SPA, built to static assets, served by FastAPI (`StaticFiles`) from a single container. Keep TanStack Query + TanStack Table + Tailwind + shadcn/ui as specified. This makes the deployment story exactly what §5 wants: `docker run paperwrench`, one service, two required env vars.

#### Challenge 2 — `bulk_edit` cannot do the MVP's flagship operation

**Verified against source.** `/api/documents/bulk_edit/` supports `set_correspondent`, `set_document_type`, `set_storage_path`, `add_tag`, `remove_tag`, `modify_tags`, `modify_custom_fields`, `delete`, `reprocess`, `set_permissions`. **There is no `set_title`**, and no generic "set field" method.

The headline MVP scenario is a *title* template transformation. Therefore the primary write path for the MVP is **per-document `PATCH /api/documents/{id}/`**, not `bulk_edit`.

This is fine, and arguably better:

- `PATCH` is **synchronous** and returns the updated object, so we can record what was *actually written* — which is precisely what rollback conflict detection needs.
- `bulk_edit` is explicitly **asynchronous** ("executed asynchronously"); it returns before the work is done, so a read-back immediately afterwards may observe stale data and per-document success/failure is not reported.

**Architectural decision (ADR-worthy):** the MVP Transformation Engine writes via per-document `PATCH` with bounded concurrency. `bulk_edit` may be adopted later as an *optimisation* for the specific ops it supports (tags, custom fields), but only behind the same Job/Operation records, and only once we solve verification of async completion. Do not design the engine around `bulk_edit`.

#### Challenge 3 — Revise the Job state model (§13)

The suggested list (`PENDING, RUNNING, COMPLETED, PARTIAL, FAILED, ROLLING_BACK, ROLLED_BACK`) mixes two orthogonal concerns: *execution lifecycle* and *rollback relationship*. Since §15 also says "rollback should itself be a Job", `ROLLING_BACK`/`ROLLED_BACK` would be derived state on the original job, duplicated from the rollback job's own status — two sources of truth for the same fact.

**Recommendation:**

```
JobStatus = PENDING | RUNNING | COMPLETED | PARTIAL | FAILED | CANCELLED | INTERRUPTED
```

- `PARTIAL` = finished, ≥1 success and ≥1 failure.
- `FAILED` = finished, zero successes.
- `INTERRUPTED` = process died mid-run (see H); resumable, and honest rather than a job stuck at `RUNNING` forever.
- `CANCELLED` = user stopped it; already-applied operations remain recorded.

Rollback state is expressed **relationally**: `Job.rollback_of_job_id` and `JobOperation.rollback_of_operation_id`. "Is this job rolled back?" becomes a query, and partial rollbacks (12 of 124 reverted, 3 conflicted) are representable — which the flat enum cannot do.

#### Challenge 4 — Nested `ANY` across *different* core fields is not compilable server-side

This is the most consequential architectural constraint I found, and the spec's §8 example needs a caveat.

Paperless core-field filters are django-filter query parameters, and **multiple query parameters are always ANDed**. There is no server-side OR across heterogeneous core fields. So:

- ✅ `ANY(Correspondent IS EDF, Correspondent IS Engie)` — compilable, becomes `correspondent__id__in=3,7`. (This is the spec's actual example, so it works.)
- ✅ Arbitrary nested `AND`/`OR`/`NOT` over **custom fields** — compilable, `custom_field_query` genuinely supports it (depth ≤ 10, ≤ 20 atoms).
- ❌ `ANY(Correspondent IS EDF, Tag CONTAINS Urgent)` — **not compilable**. Only client-side evaluation could answer it, which means fetching the whole library. Forbidden by §41.

**Recommendation:** define a *compilable subset* explicitly and make the compiler total-or-explicit: it either returns Paperless query params, or raises `FilterNotCompilable` with a human-readable reason that the UI surfaces ("OR across different metadata fields isn't supported yet"). The FilterSet *model* still supports arbitrary nesting (per §44), so nothing is painted into a corner — a future Paperless capability, or an opt-in bounded local evaluation for small result sets, can widen the subset without a model migration.

This also means the FilterSet **builder UI must not offer** filter shapes the compiler will reject. Constrain the UI to the compilable subset in MVP; that's a smaller UI *and* an honest one.

#### Challenge 5 — Ordering is a whitelist, and "sort by custom field" needs the field ID

**Verified.** `DocumentViewSet.ordering_fields = (id, title, correspondent__name, document_type__name, storage_path__name, created, modified, added, archive_serial_number, num_notes, owner, page_count, custom_field_)`.

Good news: sorting by a custom field **is** supported server-side, via `?ordering=custom_field_<id>` (Paperless annotates a subquery on the right typed column). Constraint: it is one custom field at a time, by numeric ID, and anything outside the whitelist (e.g. `original_filename`) is not sortable. Explorer's column headers must therefore derive sortability from this whitelist rather than assuming every column sorts — otherwise users get silently unsorted grids.

#### Challenge 6 — `Schema.expected_metadata` should not be a separate concept

§16 proposes `required fields` plus `expected metadata` (`Correspondent = SDIS`). Two mechanisms for "this document should look like X" will diverge. Model both as a single ordered list of typed **rules**:

```
{ kind: "required", target: FieldRef }
{ kind: "equals",   target: FieldRef, value: ... }
```

MVP implements exactly those two kinds; §21's future rules (`min`, `max`, `regex`, `date_range`, `unique`, `expected_tags`) slot in as new `kind` values with no schema-table migration. This is strictly simpler than the spec *and* more extensible.

#### Challenge 7 — Keep tag transformations out of MVP (and know why)

**Verified in `DocumentSerializer.update`:** PATCHing `tags` applies **tag-hierarchy side effects** — adding a child auto-adds all ancestors; removing a parent removes all descendants. Also, `remove_inbox_tags` can strip inbox tags. Consequently, the tag set you write is *not necessarily* the tag set that lands, and a naive tag rollback could remove tags a user legitimately added.

§11 already defers `ADD TAG`/`REMOVE TAG` to post-MVP — good. I want the *reason* recorded: tag writes are non-trivially non-idempotent, so when we do implement them, the Job must record the **post-write observed tag set** from the PATCH response, not the intended one. (The generic mechanism in D/H already does exactly this, so we're not blocked.)

### B.3 Missing concerns in the spec (these are the blockers)

#### 🔴 Blocker 1 — PATCHing `custom_fields` is destructive. It deletes omitted fields.

**Verified in source.** `DocumentSerializer` inherits `drf_writable_nested.NestedUpdateMixin`. Its `update()` calls `delete_reverse_relations_if_need()`, which computes:

```python
pks_to_delete = list(
    model_class.objects.filter(**related_field_lookup)
               .exclude(pk__in=current_ids)
               .values_list('pk', flat=True)
)
```

Meaning: **any `CustomFieldInstance` not present in the submitted `custom_fields` array is deleted.** `custom_fields` behaves as a full replacement, not a merge — despite `PATCH` semantics implying otherwise everywhere else in the payload.

Practical consequence: a "set Montant" transformation naively implemented as

```json
PATCH /api/documents/184/  { "custom_fields": [ {"field": 7, "value": "482.31"} ] }
```

**silently destroys `Période concernée` and every other custom field on that document.** On a 124-document dry-run-approved job, that is 124 documents of unrecoverable-by-us data loss, and Paperless' own audit log would record it as our deliberate act.

**Required mitigations (all of them):**

1. Every custom-field write is **read-modify-write**: fetch the document's current full `custom_fields` array, apply the change, submit the complete array. Never construct a partial array.
2. The `PaperlessClient` must not expose a raw "patch custom field" primitive. It exposes `update_document_fields(doc_id, changes, *, base_document)` which builds the full array internally. Make the unsafe thing unrepresentable in our own API.
3. A **regression test** asserts that setting one custom field preserves all others (against a mocked Paperless reproducing the replace semantics, plus an integration test against a live instance).
4. The Job records the **complete before-state of all custom fields**, not just the targeted one, so rollback can restore a document even if this rule is ever violated.

This single finding justifies the whole review step.

#### 🔴 Blocker 2 — Record what was *written*, not what was *intended*

For rollback conflict detection to be sound, the "after" value must be the value **Paperless confirmed**, not the value we hoped to set. Paperless normalises input: `created` is coerced from datetime strings to a date (API v9+); monetary values may be stored with a 3-char ISO-4217 currency prefix (`"USD100.00"`) and are compared numerically via a generated `value_monetary_amount` column; select fields store the option **id** while the UI shows the label; tag hierarchy rewrites tag sets.

If we store the intended value, then on rollback `current == intended` will be **false for correctly-written documents**, and we will report phantom conflicts on exactly the documents that worked.

**Requirement:** `JobOperation` stores three values — `before_value`, `intended_value`, `written_value` (parsed from the PATCH response body). Conflict detection compares `current_value == written_value`. §15 says "verify the current value still equals the value originally written by the job" — this makes that literally true rather than approximately true.

#### 🔴 Blocker 3 — Single-process assumption must be enforced, not assumed

The in-process job engine (§H, no Celery) is only safe if exactly one scheduler exists. Running Uvicorn/Gunicorn with `--workers 2` would give N schedulers racing over the same SQLite job rows, double-executing operations against Paperless.

**Requirement:** the container entrypoint pins a single worker, SQLite runs in WAL mode with a busy timeout, and startup acquires an advisory single-instance lock (a row in a `runtime_lock` table with a PID/boot-id, or an OS file lock) that fails fast with a clear error if a second instance appears. Document it in `docs/architecture.md` as a deliberate MVP constraint with a defined upgrade path (§H.6).

#### 🟡 Other gaps worth closing in MVP

- **Trash / soft delete.** Documents have `deleted_at`. A job targeting a document that is later trashed must fail cleanly (and rollback must not resurrect values on a trashed doc). Filter trashed docs out of datasets by default.
- **Permissions.** Paperless has object-level permissions; the API returns `user_can_change`. A single-token PaperWrench acts as one Paperless user, so a bulk job can legitimately fail with 403 on individual documents. The Job model must treat 403 as a normal per-operation failure, and the dry run should ideally warn using `user_can_change`.
- **Concurrent edits *during* a job.** Between dry run and apply, a document may change. The dry run stores the `modified` timestamp / before-value it based its preview on; at apply time, if the before-value no longer matches, the operation is a `SKIPPED_CONFLICT`, not a blind overwrite. This is the same guard as rollback, applied forward.
- **Rate limiting / load.** §41 mentions bounded concurrency; the spec omits retry policy. Retry only idempotent-safe failures (timeouts, 5xx, 429 with backoff); never blind-retry a 4xx.
- **Custom field `select` rendering.** Values are option IDs; templates and grids must resolve id → label, and a template that emitted a raw UUID-ish id into a title would be a visible product bug.
- **API v9 vs v10 divergence.** We should pin v10 and negotiate explicitly (see C.2), and the compatibility check must fail loudly on unsupported servers.

### B.4 Unnecessary complexity to avoid in MVP

- Next.js (B.2 Challenge 1).
- A plugin/provider abstraction for AI. §23 already says don't; agreed — a single well-named module boundary is enough.
- A generic expression/DSL parser for templates. `{Field Name}` placeholder substitution with strict validation is sufficient; do not build an expression language for §28 computed fields yet.
- An event bus / domain-event layer. Direct function calls between Filter → Transform → Job are clearer at this size.
- Multi-user auth and RBAC inside PaperWrench (§38 excludes it). MVP assumes a trusted single-tenant deployment behind the user's own network. **This must be stated loudly in the README**, because a naive "expose it to the internet" deployment would hand anybody full write access to the archive.
- Repository/UnitOfWork abstractions over SQLAlchemy. SQLAlchemy is already that.

### B.5 Where current Paperless capabilities simplify the design

Several things the spec plans to build are already server-side, and using them keeps us honest about §41:

| Need | Paperless already provides |
|---|---|
| Full-text search | `?query=` (Tantivy), `?text=`, `?title_search=`, `?more_like_id=` |
| Nested custom-field logic | `?custom_field_query=["AND",[...]]` with `OR`/`NOT`, depth ≤10 |
| "Custom field is empty" | `["OR",[["f","isnull",true],["f","exact",""]]]` |
| "Document lacks field entirely" | `["f","exists",false]` |
| Sort by custom field | `?ordering=custom_field_<id>` |
| Metadata usage counts (future Metadata Manager) | `document_count` on tag/correspondent/type/custom-field serializers |
| Exact duplicate detection (future Duplicate Center) | `duplicate_documents` on the document serializer + `checksum` filters |
| Document versions (future Version Manager) | full versions API incl. `merge_as_versions` |
| Inbox view (future Review Queue) | `?is_in_inbox=true` |

The `document_count` and `duplicate_documents` findings in particular mean two roadmap features are far cheaper than the spec assumes. Per §36 we should not rebuild them.

---

## C. Paperless API verification

Legend: ✅ **Verified** (official docs and/or `main` source read during this review) · 🟡 **Assumption** (reasonable, unverified) · 🧪 **Needs live-instance experiment**

### C.1 Authentication ✅

Five mechanisms; we use **token**: `Authorization: Token <token>`. Tokens are created in *My Profile* in the Paperless UI, or obtained by POSTing credentials to `/api/token/`.

**Decision:** PaperWrench accepts a pre-created token via `PAPERLESS_TOKEN` only. We do **not** implement `/api/token/` credential exchange in MVP — storing a user's Paperless password to mint tokens is a needless secret-handling liability.

### C.2 API versioning ✅

- Versioned via header: `Accept: application/json; version=10`.
- Supported versions **9 and 10**; default when unspecified is **10**.
- Invalid version → **406 Not Acceptable**.
- Every response to an authenticated request carries `X-Api-Version` and `X-Version` (server version).
- Deprecation policy: old versions supported ≥1 year after a new one ships.

**Decision:** pin `PAPERWRENCH_PAPERLESS_API_VERSION=10`, send the `Accept` header on **every** request (never rely on the server default, which will move), and implement the compatibility check exactly as documented: one authenticated request, then inspect `X-Api-Version`/`X-Version`. Absent headers ⇒ too old / not Paperless ⇒ actionable startup error. `GET /api/remote_version/` is *not* used for this (it reports upstream release info, not the local contract).

### C.3 Document listing & pagination ✅

`GET /api/documents/` → `{count, next, previous, results[]}`, `StandardPagination`: `page_size = 25` default, `page_size_query_param = "page_size"`, `max_page_size = 100000`.

The `all` key (list of every matching ID) is present only for API < 10 and is **deprecated**.

**Decisions:** Explorer requests `page_size` 100 (tunable, capped ~250) — large enough to be snappy, far from `max_page_size` abuse. For "apply to all N matching documents" we must **not** use `all`; instead the backend re-runs the compiled filter server-side and pages through IDs at a large page size to materialise the target set at job-creation time (snapshotting it into the Job, so the target set can't shift mid-run).

### C.4 Search ✅

- `?text=` — simple substring over title + content
- `?title_search=` — title only
- `?query=` — full Tantivy query syntax
- `?more_like_id=` — similarity
- `?title_content=` — **deprecated** (v10), do not use
- Search results add `__search_hit__` (`score`, `highlights`, `rank`); pagination works normally
- `GET /api/search/autocomplete/?term=&limit=`

**Decision:** Explorer's search box uses `text` by default with an opt-in "advanced query" mode mapping to `query`. 🧪 Worth confirming on a live instance how freely search combines with heavy filtering + custom-field ordering, since search results come out of the Tantivy index and ordering is partly index-driven.

### C.5 Filtering ✅

`DocumentFilterSet` lookups, verified from source:

```
CHAR_KWARGS     = istartswith, iendswith, icontains, iexact
ID_KWARGS       = in, exact
INT_KWARGS      = exact, gt, gte, lt, lte, isnull
DATE_KWARGS     = year, month, day, gt, gte, lt, lte
DATETIME_KWARGS = year, month, day, date__gt, date__gte, gt, gte, date__lt, date__lte, lt, lte
```

Applied to: `id`, `title`, `archive_serial_number`, `created`, `added`, `modified`, `original_filename`, `checksum`, `correspondent{,__id,__name}`, `tags__id`, `tags__name`, `document_type{,__id,__name}`, `storage_path{,__id,__name}`, `owner{,__id}`.

Plus explicit filters: `is_tagged`, `tags__id__all`, `tags__id__none`, `tags__id__in`, `correspondent__id__none`, `document_type__id__none`, `storage_path__id__none`, `is_in_inbox`, `content__icontains|istartswith|iendswith|iexact`, `owner__id__none`, `custom_fields__id__all|none|in`, `has_custom_fields`, `custom_field_query`, `shared_by__id`, `mime_type`, and back-compat `created__date__{gt,gte,lt,lte}`.

**Key limitation (B.2 Challenge 4):** query params are ANDed; no server-side OR across different core fields.

### C.6 Ordering ✅

Whitelist: `id`, `title`, `correspondent__name`, `document_type__name`, `storage_path__name`, `created`, `modified`, `added`, `archive_serial_number`, `num_notes`, `owner`, `page_count`, and the `custom_field_` prefix.

`?ordering=custom_field_<id>` sorts on the correct typed column per data type (`value_text`, `value_int`, `value_float`, `value_date`, `value_monetary_amount`, and a synthesised label-ordering for `select`). Unknown custom field ID → validation error.

### C.7 Custom fields ✅

- `GET /api/custom_fields/` → `id`, `name`, `data_type`, `extra_data`, `document_count`
- Data types: `string`, `url`, `date`, `boolean`, `integer`, `float`, `monetary`, `documentlink`, `select`, `longtext`
- On a document: `custom_fields: [{field: <id>, value: <any>}]`
- `select` values are the option **`id`** (API v7+); options are objects `{id, label}`
- `monetary` stored as `CharField` possibly prefixed with a 3-char ISO-4217 code; a generated `value_monetary_amount` column backs numeric comparison
- `custom_field_query` operator support by type: all types get `exact/in/isnull/exists`; string/url/monetary add `icontains/istartswith/iendswith`; int/float/date/monetary add `gt/gte/lt/lte/range`; documentlink adds `contains`; limits depth ≤ 10, atoms ≤ 20; field referenced by name or ID

### C.8 Document update (PATCH) ✅

`PATCH /api/documents/{id}/` — synchronous, returns the updated document (with `full_perms` forced on for PATCH/PUT). Writable: `title`, `correspondent`, `document_type`, `storage_path`, `tags`, `created`, `archive_serial_number`, `content`, `owner`, `set_permissions`, `custom_fields`, `remove_inbox_tags`.

Notes: `created` is a **date** in v9+ (`created_date` deprecated); ISO datetime strings are coerced. ASN collisions with **trashed** documents raise a validation error.

### C.9 🔴 PATCH `custom_fields` replaces the entire set ✅ (verified in source)

See B.3 Blocker 1. `NestedUpdateMixin.delete_reverse_relations_if_need()` deletes every `CustomFieldInstance` absent from the payload. Read-modify-write is **mandatory**.

### C.10 🔴 PATCH `tags` has hierarchy side effects ✅ (verified in source)

`DocumentSerializer.update` adds all ancestors of requested tags and removes all descendants of removed tags; `remove_inbox_tags` can strip inbox tags. Written tag set ≠ requested tag set in general. Reinforces B.3 Blocker 2 (record the written value).

### C.11 Bulk editing ✅

`POST /api/documents/bulk_edit/` with `{documents, method, parameters}`. Methods: `set_correspondent`, `set_document_type`, `set_storage_path`, `add_tag`, `remove_tag`, `modify_tags`, `delete`, `reprocess`, `set_permissions`, `modify_custom_fields`. **Asynchronous.** **No `set_title`.**

`modify_custom_fields` takes `add_custom_fields` (`{id: value}` or `[ids]`) and `remove_custom_fields` (`[ids]`) — notably this **is** a merge-style API, unlike PATCH. Worth revisiting post-MVP as the safer/faster custom-field write path once async verification is solved.

Also: `/api/bulk_edit_objects/` for tags/correspondents/types/storage paths (delete + set_permissions), which gains `all`/`filters` params in v10 — relevant to the future Metadata Manager. In v10, doc-editing ops (`merge`, `rotate`, `edit_pdf`) moved to dedicated endpoints; the `bulk_edit` route is deprecated for those.

### C.12 Preview / download / thumbnail / metadata ✅

- `GET /api/documents/{id}/preview/`
- `GET /api/documents/{id}/download/` (`?original=true` for the original file)
- `GET /api/documents/{id}/thumb/`
- `GET /api/documents/{id}/metadata/`

All accept `?version={version_id}`.

**Decision:** the browser never receives the Paperless token, so these are **proxied** through PaperWrench with streaming responses (see I.7).

### C.13 Tags / correspondents / document types / storage paths ✅

`/api/tags/`, `/api/correspondents/`, `/api/document_types/`, `/api/storage_paths/` — paginated, filterable (`name` CHAR_KWARGS, `id` ID_KWARGS, matching config), and each serializer exposes **`document_count`**. Tags additionally expose `color`/`text_color` (v2+) and hierarchy.

### C.14 Other verified details

- `?full_perms=true` returns full object permissions; document serializer exposes `user_can_change`.
- Document serializer fields include `page_count`, `mime_type`, `duplicate_documents`, `notes`, `deleted_at`, `root_document`, `versions`.
- `GET /api/documents/{id}/` resolves `content` to the latest version by default; `?version=` selects another.
- Tasks: v10 paginates `/api/tasks/`, renames `task_name`→`task_type` and `type`→`trigger_source`, and adds `/api/tasks/summary/`, `/status_counts/`, `/active/`.

### C.15 Assumptions (🟡)

1. Paperless is reachable over a private Docker network with a stable base URL and no aggressive proxy timeouts on preview streaming.
2. `PATCH` on a document is atomic per request from our perspective (no partial field application) — implied by Django's transactional request handling.
3. Token auth is not rate-limited by Paperless itself; any limiting comes from a user's reverse proxy.
4. `page_size=100` is well within acceptable server load for typical self-hosted instances.

### C.16 Needs live-instance experimentation (🧪)

1. **Practical write throughput** — the right default for bounded concurrency (starting hypothesis: 4–5 concurrent PATCHes) and how Paperless behaves under a sustained 1000-document job, including whether each PATCH triggers search-index churn that degrades throughput.
2. **Search + filter + custom-field ordering interaction** — whether `?query=` composes cleanly with `custom_field_query` and `ordering=custom_field_<id>`, or whether the Tantivy path constrains ordering.
3. **Monetary round-trip** — exactly what a `monetary` field returns after writing `"482.31"` vs `"EUR482.31"`, which determines `written_value` normalisation and template rendering.
4. **Select field round-trip** — confirm writing the option `id` and reading back `id`, and confirm label resolution from `extra_data.select_options`.
5. **Empty-vs-absent custom fields** — behavioural difference between a field with `""`/`null` and a field with no instance, and which one `["f","exists",false]` vs `isnull` catches. Directly drives Quality's "Missing Amount" count correctness.
6. **Confirm the destructive `custom_fields` PATCH** empirically (C.9) — verified in source; must also be verified live before we trust the mitigation.
7. **403 behaviour** on documents the token's user cannot change, to confirm per-operation failure handling.
8. **`created` timezone/date coercion** round-trip for `written_value` comparison.

---

## D. Domain model (MVP only)

Pydantic v2 models, `from __future__ import annotations`, discriminated unions where useful. Naming stays Paperless-agnostic; the Paperless-specific shape lives only in the client + compiler.

### D.1 FilterSet / FilterCondition

```python
FieldKind   = Literal["core", "custom_field"]
CoreField   = Literal["title","content","correspondent","document_type",
                      "storage_path","tags","created","added","modified",
                      "archive_serial_number","owner","mime_type","id"]

class FieldRef(BaseModel):
    kind: FieldKind
    key: str | int          # CoreField name, or custom field ID

Operator = Literal[
    "is","is_not","in","not_in",
    "contains","not_contains",          # tags / doclink membership
    "icontains","istartswith","iendswith","iexact",
    "gt","gte","lt","lte","range",
    "is_empty","is_not_empty",          # -> isnull/"" OR-pair
    "exists","not_exists",              # custom field instance presence
]

class FilterCondition(BaseModel):
    type: Literal["condition"] = "condition"
    field: FieldRef
    operator: Operator
    value: JsonValue | None = None

class FilterGroup(BaseModel):
    type: Literal["group"] = "group"
    op: Literal["ALL","ANY","NONE"]
    children: list[FilterNode]

FilterNode = Annotated[FilterCondition | FilterGroup, Field(discriminator="type")]

class FilterSet(BaseModel):
    version: Literal[1] = 1
    root: FilterGroup
```

Nested groups are representable from day one (§44) even though the MVP compiler accepts only the subset in B.2 Challenge 4.

**Compiler** (`filters/compiler.py`), the only place that knows Paperless query syntax:

```python
class PaperlessQuery(BaseModel):
    params: dict[str, str]           # django-filter params
    custom_field_query: str | None   # JSON string

def compile_filterset(fs: FilterSet, fields: CustomFieldRegistry) -> PaperlessQuery
# raises FilterNotCompilable(reason, path) — never silently degrades
```

### D.2 Transformation

```python
class SetOp(BaseModel):
    op: Literal["SET"]; target: FieldRef; value: JsonValue

class ClearOp(BaseModel):
    op: Literal["CLEAR"]; target: FieldRef

class ReplaceOp(BaseModel):
    op: Literal["REPLACE"]; target: FieldRef
    search: str; replacement: str
    match_mode: Literal["substring","whole_value"] = "substring"
    case_sensitive: bool = True

class TemplateOp(BaseModel):
    op: Literal["TEMPLATE"]; target: FieldRef
    template: str                          # "Relevé de vacations – {Période concernée}"
    on_missing: Literal["error","skip"] = "error"

TransformationOp = Annotated[SetOp|ClearOp|ReplaceOp|TemplateOp, Field(discriminator="op")]

class Transformation(BaseModel):
    version: Literal[1] = 1
    operations: list[TransformationOp]     # list now; composite recipes later
```

`on_missing` defaults to `error` per §11's "never silently create malformed titles". Templates use `{Field Name}` for custom fields and reserved `{title}`, `{created}`, `{correspondent}`, `{document_type}` for core fields; unknown placeholders are a **validation** error at definition time, unresolvable values are a **per-document preview** error.

### D.3 TransformationPreview (Dry Run)

```python
class FieldChange(BaseModel):
    field: FieldRef
    before: JsonValue | None
    after: JsonValue | None

class PreviewRowStatus(str, Enum):
    CHANGED = "changed"; UNCHANGED = "unchanged"
    ERROR = "error"; NOT_PERMITTED = "not_permitted"

class PreviewRow(BaseModel):
    document_id: int
    document_title: str
    status: PreviewRowStatus
    changes: list[FieldChange] = []
    error: str | None = None
    source_modified: datetime | None = None   # forward conflict guard

class TransformationPreview(BaseModel):
    matched: int; changed: int; unchanged: int; errors: int; not_permitted: int
    rows: list[PreviewRow]          # paginated
    truncated: bool
    filterset: FilterSet | None
    transformation: Transformation
```

### D.4 Job / JobOperation

```python
class JobType(str, Enum):
    TRANSFORM = "transform"; ROLLBACK = "rollback"

class JobStatus(str, Enum):
    PENDING="pending"; RUNNING="running"; COMPLETED="completed"
    PARTIAL="partial"; FAILED="failed"; CANCELLED="cancelled"
    INTERRUPTED="interrupted"

class OperationStatus(str, Enum):
    PENDING="pending"; SUCCEEDED="succeeded"; FAILED="failed"
    SKIPPED_UNCHANGED="skipped_unchanged"
    SKIPPED_CONFLICT="skipped_conflict"      # value moved since preview
    SKIPPED_PERMISSION="skipped_permission"

class JobOperation(BaseModel):
    id: int; job_id: int
    document_id: int
    field: FieldRef
    before_value: JsonValue | None
    intended_value: JsonValue | None
    written_value: JsonValue | None      # from the PATCH response (Blocker 2)
    status: OperationStatus
    error: str | None
    http_status: int | None
    attempts: int
    rollback_of_operation_id: int | None
    started_at / finished_at: datetime | None

class Job(BaseModel):
    id: int
    type: JobType
    status: JobStatus
    title: str
    filterset: FilterSet | None
    transformation: Transformation | None
    document_ids: list[int]                # snapshot at creation
    rollback_of_job_id: int | None
    counts: JobCounts                      # total/succeeded/failed/skipped/pending
    paperless_api_version: int
    paperless_server_version: str | None
    created_at / started_at / finished_at
    created_by: str | None                 # reserved, single-user MVP
```

One `JobOperation` **per document per field** — the finest granularity that supports partial failure and per-field rollback, and it maps 1:1 to the History UI.

### D.5 Schema

```python
class RequiredRule(BaseModel):
    kind: Literal["required"]; target: FieldRef

class EqualsRule(BaseModel):
    kind: Literal["equals"]; target: FieldRef; value: JsonValue

SchemaRule = Annotated[RequiredRule | EqualsRule, Field(discriminator="kind")]

class DocumentSchema(BaseModel):
    id: int | None; name: str
    applies_when: FilterSet
    rules: list[SchemaRule]
    description: str | None
```

Per B.2 Challenge 6, `expected_metadata` is folded into `rules`.

### D.6 Collection

```python
class Collection(BaseModel):
    id: int | None
    name: str
    kind: Literal["static"] = "static"     # "dynamic" reserved for FilterSet-backed
    filterset: FilterSet | None = None     # unused in MVP; column exists
    document_ids: list[int]                # via join table
    count: int
```

`kind` + a nullable `filterset` column make dynamic collections a pure additive change later — the §18 requirement — without implementing them now.

### D.7 Normalized document model

```python
class DocumentCustomFieldValue(BaseModel):
    field_id: int; name: str
    data_type: CustomFieldDataType
    value: JsonValue | None
    display_value: str | None      # select label / monetary formatting

class Document(BaseModel):
    id: int; title: str
    correspondent: EntityRef | None
    document_type: EntityRef | None
    storage_path: EntityRef | None
    tags: list[EntityRef]
    created: date
    added / modified: datetime
    archive_serial_number: int | None
    page_count: int | None
    mime_type: str | None
    custom_fields: list[DocumentCustomFieldValue]
    user_can_change: bool
    has_versions: bool
    raw_custom_fields: list[dict]   # verbatim, for safe read-modify-write (C.9)
```

`raw_custom_fields` is deliberate: it guarantees a lossless full array for rewrite even if our normalisation ever loses a nuance.

---

## E. SQLite model

Principle: **PaperWrench persists its own concepts and the audit trail of its own actions. Nothing else.** SQLAlchemy 2.0 typed ORM + Alembic migrations from commit one (§39).

```sql
-- Alembic owns schema_version

CREATE TABLE settings (               -- singleton row, id=1
  id INTEGER PRIMARY KEY CHECK (id = 1),
  paperless_url TEXT,                 -- may override env
  default_page_size INTEGER DEFAULT 100,
  max_concurrency INTEGER DEFAULT 4,
  theme TEXT DEFAULT 'system',
  updated_at TEXT NOT NULL
);                                    -- NOTE: no token column, see I.2

CREATE TABLE jobs (
  id INTEGER PRIMARY KEY,
  type TEXT NOT NULL,                 -- transform | rollback
  status TEXT NOT NULL,
  title TEXT NOT NULL,
  filterset_json TEXT,
  transformation_json TEXT,
  document_ids_json TEXT NOT NULL,    -- snapshot of target set
  rollback_of_job_id INTEGER REFERENCES jobs(id),
  total_count INTEGER NOT NULL DEFAULT 0,
  succeeded_count INTEGER NOT NULL DEFAULT 0,
  failed_count INTEGER NOT NULL DEFAULT 0,
  skipped_count INTEGER NOT NULL DEFAULT 0,
  paperless_api_version INTEGER,
  paperless_server_version TEXT,
  error TEXT,
  created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
  heartbeat_at TEXT                   -- interruption detection (H.2)
);
CREATE INDEX ix_jobs_status ON jobs(status);
CREATE INDEX ix_jobs_created_at ON jobs(created_at DESC);

CREATE TABLE job_operations (
  id INTEGER PRIMARY KEY,
  job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  document_id INTEGER NOT NULL,
  field_kind TEXT NOT NULL,           -- core | custom_field
  field_key TEXT NOT NULL,
  before_value_json TEXT,
  intended_value_json TEXT,
  written_value_json TEXT,            -- what Paperless confirmed (Blocker 2)
  before_custom_fields_json TEXT,     -- full CF snapshot (Blocker 1 safety net)
  source_modified TEXT,               -- doc 'modified' at preview time
  status TEXT NOT NULL,
  error TEXT, http_status INTEGER,
  attempts INTEGER NOT NULL DEFAULT 0,
  rollback_of_operation_id INTEGER REFERENCES job_operations(id),
  started_at TEXT, finished_at TEXT
);
CREATE INDEX ix_ops_job ON job_operations(job_id, status);
CREATE INDEX ix_ops_doc ON job_operations(document_id);
CREATE UNIQUE INDEX ux_ops_job_doc_field
  ON job_operations(job_id, document_id, field_kind, field_key);  -- idempotency

CREATE TABLE schemas (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  description TEXT,
  applies_when_json TEXT NOT NULL,
  rules_json TEXT NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);

CREATE TABLE collections (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  kind TEXT NOT NULL DEFAULT 'static',
  filterset_json TEXT,                -- reserved for dynamic collections
  description TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);

CREATE TABLE collection_documents (
  collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
  document_id INTEGER NOT NULL,       -- Paperless ID; intentionally no FK
  added_at TEXT NOT NULL,
  PRIMARY KEY (collection_id, document_id)
);

CREATE TABLE runtime_lock (           -- single-instance guard (Blocker 3)
  id INTEGER PRIMARY KEY CHECK (id = 1),
  instance_id TEXT NOT NULL,
  acquired_at TEXT NOT NULL,
  heartbeat_at TEXT NOT NULL
);
```

**Deliberately NOT persisted (Paperless owns it):**

documents, OCR/content, titles, dates, tags, correspondents, document types, storage paths, custom field definitions *or* values, files/thumbnails, permissions/users, Paperless' own audit log, saved views, and any document list "cache".

The only document data we store is **historical evidence inside `job_operations`** — a record of a past state at the moment we acted on it. It is never read as current truth; it is only ever compared against freshly fetched values. Custom field definitions are fetched live and held in a short-lived in-process TTL cache (registry for name→id resolution), never written to disk.

`document_ids_json` and `collection_documents` store Paperless IDs with no referential integrity, so deletions in Paperless surface as clean per-operation failures rather than corrupt joins.

**Engine settings:** `PRAGMA journal_mode=WAL`, `busy_timeout=5000`, `foreign_keys=ON`, `synchronous=NORMAL`.

---

## F. Repository structure

```
paperwrench/
├── README.md                    # incl. "not affiliated with Paperless-ngx"
├── LICENSE                      # (see L.3)
├── CHANGELOG.md
├── .env.example
├── docker-compose.yml           # paperwrench alone (attach to existing paperless)
├── docker-compose.dev.yml       # + full paperless stack for development
├── Dockerfile                   # multi-stage: node build -> python runtime
├── Makefile                     # dev / test / lint / typecheck / build
│
├── backend/
│   ├── pyproject.toml           # uv/hatch; ruff + mypy config
│   ├── alembic.ini
│   ├── migrations/versions/
│   └── src/paperwrench/
│       ├── main.py              # FastAPI app factory, lifespan, SPA mount
│       ├── config.py            # pydantic-settings, PAPERWRENCH_* / PAPERLESS_*
│       ├── logging.py           # structlog + token redaction filter
│       ├── db/                  # engine, session, models/, base
│       ├── paperless/
│       │   ├── client.py        # PaperlessClient (only HTTP to Paperless)
│       │   ├── compat.py        # version negotiation + startup check
│       │   ├── mappers.py       # raw JSON -> normalized domain models
│       │   ├── errors.py
│       │   └── registry.py      # custom field / tag / correspondent registry
│       ├── domain/              # filterset, transformation, document, schema, jobs
│       ├── filters/compiler.py  # FilterSet -> PaperlessQuery (only place)
│       ├── transform/
│       │   ├── template.py      # placeholder parse/render/validate
│       │   ├── engine.py        # apply ops to a document -> FieldChange[]
│       │   └── preview.py       # dry run
│       ├── jobs/
│       │   ├── engine.py        # asyncio runner, bounded concurrency
│       │   ├── executor.py      # per-operation write + verify
│       │   ├── rollback.py      # inverse job + conflict detection
│       │   ├── recovery.py      # startup INTERRUPTED sweep / resume
│       │   └── repository.py
│       ├── quality/evaluator.py # Schema x FilterSet -> QualityReport
│       ├── collections/service.py
│       └── api/v1/              # routers: documents, metadata, filters,
│                                #   transform, jobs, schemas, quality,
│                                #   collections, settings, system
│
├── frontend/
│   ├── package.json             # vite, react, ts, tailwind, tanstack, shadcn
│   └── src/
│       ├── main.tsx / App.tsx / routes/
│       ├── api/                 # generated types + typed fetch client
│       ├── components/{ui,explorer,inspector,transform,jobs,filters,layout}/
│       ├── hooks/
│       ├── lib/
│       └── styles/
│
├── tests/
│   ├── backend/
│   │   ├── unit/                # compiler, template, preview, rollback logic
│   │   ├── integration/         # respx-mocked Paperless (incl. CF-replace repro)
│   │   └── live/                # opt-in, real instance (marker: live)
│   └── frontend/                # vitest + RTL
│
└── docs/
    ├── architecture.md
    ├── architecture-review.md   # this document
    ├── decisions/               # ADR-0001..N
    ├── development.md
    ├── paperless-api.md         # verified API notes + gotchas from section C
    └── roadmap.md
```

Rationale: `paperless/` is the only package importing HTTPX-to-Paperless, `filters/compiler.py` is the only translator to Paperless query syntax, and `domain/` has no Paperless imports at all. That boundary is what makes §39's "keep Paperless-specific representation isolated" enforceable — and testable with an import-linter rule.

---

## G. Backend API (PaperWrench's own, `/api/v1`)

Design rule: the frontend speaks **PaperWrench** vocabulary. Paperless query syntax, `custom_field_query` JSON and Paperless IDs-as-URLs never leak into frontend code. FilterSets are sent as JSON bodies (too complex and too long for query strings), so list endpoints that take a FilterSet use `POST .../search`.

### System & settings
```
GET    /api/v1/system/health                → {status, db, uptime}
GET    /api/v1/system/paperless             → {reachable, api_version, server_version,
                                               compatible, warnings[], error?}
GET    /api/v1/settings                     → non-secret settings
PATCH  /api/v1/settings
POST   /api/v1/settings/test-connection     → validate URL/token server-side
```

### Metadata (proxied, normalized, cached briefly)
```
GET /api/v1/metadata/custom-fields    → [{id,name,data_type,options?,document_count}]
GET /api/v1/metadata/tags             → [{id,name,color,document_count}]
GET /api/v1/metadata/correspondents
GET /api/v1/metadata/document-types
GET /api/v1/metadata/storage-paths
GET /api/v1/metadata/filter-capabilities  → operators per field type + sortable fields
```
`filter-capabilities` lets the filter builder and grid derive what's possible from the server's verified truth (C.5/C.6) instead of duplicating the whitelist in TS.

### Documents (Explorer)
```
POST /api/v1/documents/search
  body: {filterset?, search?, search_mode?, ordering?, page, page_size, fields?}
  → {count, page, page_size, results: Document[], truncated_ordering?}

POST /api/v1/documents/ids            → {count, ids[]}   # resolve "select all matching"
GET  /api/v1/documents/{id}           → Document (Inspector)
PATCH /api/v1/documents/{id}          → inline edit; safe CF read-modify-write
GET  /api/v1/documents/{id}/preview   → streamed proxy (application/pdf)
GET  /api/v1/documents/{id}/thumbnail → streamed proxy
GET  /api/v1/documents/{id}/download  → streamed proxy (?original=true)
POST /api/v1/documents/{id}/neighbors → {previous_id, next_id}  # Inspector nav
```
`neighbors` keeps prev/next consistent with the *current dataset* (filters + ordering) without the client re-deriving it — §10's "keep Explorer context intact".

### Filters
```
POST /api/v1/filters/validate  → {compilable, reason?, path?, explanation}
POST /api/v1/filters/count     → {count}
```

### Transform & Dry Run
```
POST /api/v1/transform/validate   → template/op validation (before any doc fetch)
POST /api/v1/transform/preview
  body: {target: {filterset|document_ids}, transformation, page, page_size}
  → TransformationPreview
POST /api/v1/transform/apply
  body: {target, transformation, preview_token}
  → {job_id}       # 202
```
`preview_token` is a server-side hash of (target set + transformation + preview basis). Apply recomputes it and rejects a mismatch, which structurally enforces §12's "preview first, apply second" — you cannot apply something the user never previewed.

### Jobs & History
```
GET  /api/v1/jobs                          → paginated, filter by status/type
GET  /api/v1/jobs/{id}                     → Job + counts
GET  /api/v1/jobs/{id}/operations          → paginated, filter by status
POST /api/v1/jobs/{id}/cancel
POST /api/v1/jobs/{id}/resume              → INTERRUPTED only
GET  /api/v1/jobs/{id}/events              → SSE progress stream
POST /api/v1/jobs/{id}/rollback/preview    → RollbackPreview (incl. conflicts)
POST /api/v1/jobs/{id}/rollback            → {job_id}
```
SSE (not WebSockets) for progress: one-directional, trivially proxied, no extra deps — enough for §40's "never leave the user wondering if it's frozen". Clients poll `GET /jobs/{id}` as a fallback.

### Schemas & Quality
```
GET/POST/PATCH/DELETE /api/v1/schemas[/{id}]
POST /api/v1/schemas/{id}/preview          → applicability count
GET  /api/v1/quality/schemas/{id}/report
  → {total, valid, violations: [{rule, label, count, filterset}]}
```
Each violation returns the **FilterSet that isolates it**, so clicking "Missing Amount 4" navigates Explorer with that FilterSet — satisfying §17's "do not build a separate query mechanism for Quality" by construction.

### Collections
```
GET/POST/PATCH/DELETE /api/v1/collections[/{id}]
POST /api/v1/collections/{id}/documents      {document_ids[]} | {filterset}
DELETE /api/v1/collections/{id}/documents    {document_ids[]}
POST /api/v1/collections/{id}/search         → same shape as documents/search
```

**Errors:** uniform envelope `{error: {code, message, details?, retryable}}` with codes like `PAPERLESS_UNREACHABLE`, `PAPERLESS_INCOMPATIBLE`, `FILTER_NOT_COMPILABLE`, `TEMPLATE_UNRESOLVED`, `PREVIEW_STALE`, `JOB_CONFLICT`. OpenAPI schema is generated and TS types derived from it (`openapi-typescript`) so the contract can't drift.

---

## H. Job execution strategy (no Redis/Celery)

### H.1 Model

A single in-process **asyncio worker** owning a bounded task set, with **SQLite as the durable state machine**. Jobs are not held in memory as the source of truth — memory holds only the currently-executing operations. Every state transition is committed before the next network call.

```
POST /transform/apply
  → within one transaction:
      insert job (PENDING) + N job_operations (PENDING) with before/intended values
  → enqueue job id on an in-process asyncio.Queue
  → 202 {job_id}
worker:
  claim job (PENDING -> RUNNING, atomic UPDATE ... WHERE status='pending')
  stream PENDING operations in batches
  asyncio.Semaphore(max_concurrency) bounds in-flight PATCHes
  per operation: re-verify -> PATCH -> parse response -> commit terminal status
  heartbeat job every ~2s
  finalise: COMPLETED | PARTIAL | FAILED | CANCELLED
```

Because operations are individually durable, the engine is **crash-safe by construction**: restarting resumes from persisted per-operation state rather than replaying a job from scratch.

### H.2 Application restart / interrupted jobs

On startup (FastAPI lifespan):

1. Acquire the single-instance lock (`runtime_lock`); fail fast if held by a live instance.
2. Any job in `RUNNING` belongs to a dead process → mark **`INTERRUPTED`**. Its completed operations keep their terminal status; its `PENDING` operations stay pending.
3. Interrupted jobs are **not** auto-resumed. They surface in the UI as "Interrupted — 87 of 124 applied" with an explicit **Resume** action, and resume only re-processes `PENDING` operations.

Manual resume is the right default: after a crash the user may want to inspect state before writing more to their archive, and silent auto-resume on boot violates §40's visible-safety principle. (`PAPERWRENCH_AUTO_RESUME_JOBS=true` can opt in.)

**Unavoidable ambiguity, stated honestly:** if the process dies *between* a successful PATCH and its status commit, that operation stays `PENDING` while the write already happened. Resume handles this correctly because it **re-reads the document first**: if the current value already equals `intended_value`, the operation is recorded `SUCCEEDED` (with `written_value` from the fresh read) instead of being re-applied. That makes resume idempotent, and it's why the verify-before-write step in H.4 isn't optional.

### H.3 Progress

`jobs.succeeded_count/failed_count/skipped_count` are updated as operations complete; `heartbeat_at` proves liveness. The UI gets an SSE stream (`/jobs/{id}/events`) with throttled snapshots (~1/s) and falls back to polling. A job whose heartbeat is stale by > 30s while `RUNNING` is displayed as "possibly stalled" rather than silently spinning.

### H.4 Per-operation execution (the safety core)

```
1. GET document (fresh)
2. If trashed / missing            -> FAILED (clear reason)
3. If not user_can_change          -> SKIPPED_PERMISSION
4. If current(field) == intended   -> SUCCEEDED (idempotent no-op; record written)
5. If current(field) != before     -> SKIPPED_CONFLICT (someone edited since preview)
6. Build payload:
     - custom fields -> FULL array read-modify-write   (C.9 / Blocker 1)
     - core fields   -> minimal patch
7. PATCH
8. Parse response -> written_value                      (Blocker 2)
9. If written_value != intended    -> SUCCEEDED_NORMALIZED (record both; e.g. monetary)
10. Commit terminal status + timestamps
```

Step 5 is the forward-facing twin of rollback conflict detection: a dry run approved 10 minutes ago must not silently overwrite an edit made 5 minutes ago.

### H.5 Bounded concurrency, retries, cancellation

- `asyncio.Semaphore(PAPERWRENCH_MAX_CONCURRENCY)`, default **4** (🧪 C.16.1), hard-capped at 16 to keep users from DoSing their own Paperless.
- One shared `httpx.AsyncClient` with keep-alive limits aligned to the semaphore, explicit connect/read timeouts.
- Retries only on transport errors, 5xx, and 429 — max 3 attempts, exponential backoff with jitter, honouring `Retry-After`. **Never** retry 4xx (except 429): a 400 will fail identically forever, and retrying a 409/403 storm is how you get banned by your own reverse proxy.
- Cancellation is cooperative: set `CANCELLED`, stop claiming new operations, let in-flight ones finish and commit. Already-applied operations remain fully recorded and rollback-able.

### H.6 Reliability limitations (explicit, per the spec's request)

1. **Single process only.** Horizontal scaling is impossible without an external queue; enforced by the startup lock (Blocker 3).
2. **Jobs die with the container.** Mitigated by durable per-operation state + resume, but a job is not guaranteed to *finish* across a restart without user action.
3. **No scheduled/recurring jobs.** Not in MVP scope; would need a scheduler.
4. **Long jobs share the web process.** Async I/O keeps the event loop responsive since the work is network-bound, but a pathological CPU-bound future operation (AI, fuzzy matching) would block it. Rule: keep job work I/O-bound; anything CPU-bound goes to a thread/process pool.
5. **SQLite single-writer.** Fine at this write rate (a few ops/sec) with WAL + busy timeout.

**Simplest robust upgrade path, when justified:** keep the exact same `jobs`/`job_operations` tables and the same executor, and replace only the in-process queue + claim step with a separate worker **process** using `SKIP LOCKED`-style claiming (Postgres) or a lease column (SQLite). Because claiming is already an atomic conditional UPDATE and all state is in the database, this is a contained change — no Redis, no Celery, no rewrite. That is the extension point worth having now (§44), and it costs us nothing today.

---

## I. Security

### I.1 Threat model (state it in the README)

MVP PaperWrench is **single-tenant, trusted-network software with no built-in authentication**, holding a Paperless token with full write access to the archive. Anyone who can reach the port can bulk-modify or delete documents. It must run on a private network / behind an authenticating reverse proxy, and must **never** be exposed to the internet unprotected. Saying this plainly is part of the product.

### I.2 Paperless token handling

- Supplied via `PAPERLESS_TOKEN` env var (or Docker secret file via `PAPERLESS_TOKEN_FILE`), read once at startup into `pydantic-settings` with `SecretStr`.
- **Never** persisted to SQLite (hence no token column in `settings`), never returned by any endpoint, never included in error responses, never sent to the browser.
- `GET /system/paperless` returns only booleans/versions. `POST /settings/test-connection` validates server-side and returns a verdict, not credentials.
- Structured-logging processor redacts `Authorization`, `token`, `password`, `Cookie` keys, and the token value itself, at the formatter level so an ad-hoc `logger.info(headers)` can't leak it.
- Repo hygiene: `.env` gitignored, `.env.example` with placeholders, secret-scanning in CI.

### I.3 Docker networking

`docker-compose.yml` attaches PaperWrench to the existing Paperless network; Paperless need not be published to the host at all. PaperWrench publishes one port, and the example binds it to `127.0.0.1:8000` by default rather than `0.0.0.0` — a safe default that a user must consciously widen. Container runs as a non-root user; the SQLite volume is the only writable mount; no access to Paperless' media/data volumes (structurally enforcing §7).

### I.4 API exposure & CORS/CSRF

Single-origin deployment (FastAPI serves the built SPA), so **CORS is disabled by default** — no wildcard, no credentials. An explicit `PAPERWRENCH_CORS_ORIGINS` allowlist exists for split-origin dev only.

CSRF: with no cookie-based session (MVP has no auth), there is no session for a third-party site to ride. To keep it that way we (a) use no cookies for authorization, and (b) reject cross-origin state-changing requests via an `Origin`/`Host` check on non-GET routes. When auth arrives (v0.2+), it must be `Authorization`-header or double-submit-token based, not naked session cookies — because a cookie-authenticated PaperWrench with mutation endpoints is a CSRF-driven mass-delete waiting to happen.

Also: all mutation endpoints validate bodies with Pydantic (no `dict[str, Any]` passthrough), `page_size` is capped server-side, FilterSet nesting depth and atom count are capped before compiling (mirroring Paperless' own depth ≤10 / atoms ≤20), and the SPA's `index.html` ships a conservative CSP with no inline scripts.

### I.5 Logs

structlog JSON with a request ID and job/operation IDs. We log document IDs and field *names*, but **not** document content/OCR text, and we truncate logged field values (titles can contain personal data). Errors from Paperless are logged with status + a truncated body, never with request headers. Log level via `PAPERWRENCH_LOG_LEVEL`.

### I.6 SQLite

Contains no secrets and no document content — but it does contain metadata history (old/new titles, field values), so it is **sensitive**. It lives on a dedicated volume with `0600` file perms under the non-root app user. `foreign_keys=ON`, all ORM access parameterised (no string-built SQL). Backup guidance in the docs; no encryption at rest in MVP (out of scope, and a false sense of security given the env-var token sits next to it).

### I.7 Preview proxying

Since the browser has no token, previews are proxied: `GET /api/v1/documents/{id}/preview` streams from Paperless via `httpx.stream` into a `StreamingResponse`.

- Path parameter is `int`-typed — no user-controlled URL segments reach the Paperless URL (no SSRF pivot, no path traversal).
- Only the four documented read endpoints are proxied; there is no generic `/proxy?url=` — that would be an SSRF hole into the Docker network.
- Response headers are **allowlisted** (`Content-Type`, `Content-Length`, `Content-Disposition`, `Accept-Ranges`), forwarding no Paperless cookies or auth-related headers.
- `Content-Disposition: inline` + `X-Content-Type-Options: nosniff` for preview; `attachment` for download.
- `Cache-Control: private, no-store` so a shared browser cache never retains someone's documents.
- Upstream 4xx/5xx map to our error envelope rather than leaking raw Paperless error pages.

Rendering: native `<iframe>`/`<object>` for PDFs in MVP (zero deps, good UX). If we later need annotations/zone-extraction (§22), swap in PDF.js — the proxy contract doesn't change.

---

## J. MVP implementation plan (M0–M13)

Dependency graph — the two critical paths worth noting are `M2 → M4 → M3` (the grid needs the compiler, which needs the client) and `M6 → M7 → M8 → M9` (rollback is meaningless without durable jobs):

```
M0 ──> M1 ──> M2 ──┬──> M4 ──> M3 ──> M5
                   │                   │
                   └──> M6 ──> M7 ──> M8 ──> M9
                                       │
                              M10 ──> M11
                                       │
                              M12 ─────┴──> M13
```

**M0 — Repository & Docker foundation.** Monorepo per F; `pyproject.toml` with ruff + mypy (strict) + pytest; Vite/React/TS/Tailwind/shadcn; multi-stage Dockerfile (node build → slim python runtime, non-root); both compose files; `pydantic-settings` config with `PAPERWRENCH_*`/`PAPERLESS_*`; structlog + redaction; SQLAlchemy engine + Alembic baseline + WAL pragmas; health endpoint; CI (lint, typecheck, test, docker build); README/docs skeleton incl. the non-affiliation and threat-model notes.
*Exit:* `docker compose up` serves the SPA shell and `/api/v1/system/health` is green.

**M1 — Paperless connection & compatibility check.** Minimal client with the pinned `Accept: application/json; version=10` header; `compat.py` reading `X-Api-Version`/`X-Version`; startup non-fatal check + `GET /system/paperless`; Settings screen showing connection state with actionable errors (unreachable / 401 / 406 / unsupported); `test-connection`.
*Exit:* misconfigured token/URL produces a clear, non-leaky diagnosis in the UI.

**M2 — PaperlessClient & normalized models.** Full client: pagination iterator, typed errors, timeouts, bounded retry, `httpx.AsyncClient` lifecycle; endpoints for documents (list/get/patch), custom fields, tags, correspondents, document types, storage paths, preview/thumb/download streams; `mappers.py`; metadata registry with TTL cache + name↔id resolution; **`update_document_fields` with mandatory custom-field read-modify-write** (C.9) and its regression test.
*Exit:* respx-mocked integration tests green, incl. the CF-preservation test.

**M3 — Explorer & server-side DataGrid.** `POST /documents/search`; TanStack Table + TanStack Query grid with server pagination/sorting; dynamic custom-field columns from the registry; column visibility (persisted locally); sortability derived from `filter-capabilities`; multi-select with "select all matching" resolved server-side via `/documents/ids`; skeleton/empty/error states.
*Exit:* 20k-document instance browses smoothly; no unbounded fetches (asserted in tests).

**M4 — Filter Engine.** `FilterSet` models; compiler → `params` + `custom_field_query` with `FilterNotCompilable`; the compilable-subset rules (B.2 C4); `/filters/validate` + `/filters/count`; a filter builder UI constrained to the compilable subset; `is_empty` for custom fields as the documented `OR(isnull, exact "")` pair.
*Exit:* comprehensive compiler unit tests, incl. rejection cases with useful messages.

**M5 — Inspector.** Side-panel/split layout preserving Explorer context; proxied PDF preview; metadata + custom-field inline editing with optimistic updates and rollback-on-error; save via the safe client path; prev/next via `/neighbors`; keyboard nav (`j`/`k`, `Esc`, `Cmd+S`).
*Exit:* editing a custom field provably preserves all others against a live instance.

**M6 — Transformation Engine.** `SET`/`CLEAR`/`REPLACE`/`TEMPLATE`; template parser + strict validation + renderer with select-label and monetary handling; `engine.apply()` producing `FieldChange[]` with no I/O (pure, heavily unit-tested); `/transform/validate`.
*Exit:* template edge cases covered — missing field, empty value, unknown placeholder, literal braces, select/monetary/date formatting.

**M7 — Dry Run.** `/transform/preview` over a filterset or explicit IDs; matched/changed/unchanged/errors/not-permitted counts; paginated before→after rows; `preview_token`; the preview UI of §12 with an Apply button that is disabled unless something is actually changeable.
*Exit:* zero writes occur during preview (asserted by a mock that fails the test on any non-GET).

**M8 — Job Engine, progress & History.** Jobs/operations tables + repository; asyncio worker with semaphore; executor per H.4; SSE progress + polling fallback; single-instance lock + startup `INTERRUPTED` sweep + resume; cancellation; Jobs list, job detail with per-operation results, History views.
*Exit:* a forced kill mid-job leaves accurate persisted state and a working Resume; partial failures never erase successful history.

**M9 — Rollback & conflict detection.** Inverse-job generation from `job_operations`; rollback preview classifying each op as revertible / conflicted / already-reverted / not-applicable; conflict rule `current == written_value`; rollback executes as a normal Job with `rollback_of_job_id`; conflicts surfaced, never overwritten.
*Exit:* the conflict test — mutate a document externally after the job, then confirm rollback refuses that one and reverts the rest.

**M10 — Schemas.** CRUD + `applies_when` FilterSet + `required`/`equals` rules; applicability preview.
**M11 — Quality.** Evaluator turning schema rules into counting FilterSets (`exists=false` / `isnull` / `exact ""` / mismatch); report UI with quality percentage; **each violation links into Explorer with its FilterSet** — the §17 acceptance behaviour.
**M12 — Collections.** Static CRUD; add-from-Explorer (selection or whole filterset); browse a collection through the same grid; `kind`/`filterset` columns reserved.
**M13 — Testing, polish, docs, release.** Coverage push on critical logic (compiler, template, preview, executor, rollback); the full §20 end-to-end acceptance run against a live instance; dark mode; keyboard shortcuts; error/empty/loading audit; ADRs written up; README/architecture/paperless-api/roadmap/development finalised; v0.1.0 tag + image.

Ordering deviation from the spec, with justification: the brief lists M3 (Explorer) before M4 (Filter Engine), but Explorer's filter bar *is* a FilterSet consumer. I implement the FilterSet model and compiler first, then build the grid on top. This avoids shipping a throwaway ad-hoc filter path — precisely the duplication §2 forbids. Explorer's first vertical slice (unfiltered paginated grid) can still land in M3 in parallel.

---

## K. Acceptance tests

### K.1 The primary scenario — `Relevé de vacations` (§20), end to end

Fixture: a live Paperless with document type `Relevé de vacations`, custom fields `Période concernée` (string) and `Montant` (monetary), and ~12 documents of which 2 have an empty `Montant` and 1 an empty `Période concernée`.

| # | Step | Expected |
|---|---|---|
| 1 | Open Explorer | grid renders, server-paginated, count matches Paperless |
| 2 | Filter `Document Type IS Relevé de vacations` | compiles to `document_type__id=<id>`; count matches |
| 3 | Add `Période concernée` + `Montant` columns | values render; monetary formatted; sortable via `ordering=custom_field_<id>` |
| 4 | Filter `Montant IS EMPTY` | compiles to the `OR(isnull, exact "")` query; returns exactly the 2 seeded docs |
| 5 | Select all matching | selection resolved server-side; survives pagination |
| 6 | Define `TEMPLATE title = "Relevé de vacations – {Période concernée}"` | validates; unknown placeholder would be rejected here |
| 7 | Dry Run | matched/changed/unchanged/errors correct; **the doc with empty `Période concernée` is an ERROR row, not a malformed title** |
| 8 | Inspect preview rows | every before→after pair correct and paginated |
| 9 | Apply | 202 + job id; only non-error docs targeted; `preview_token` enforced |
| 10 | Watch progress | SSE updates; counts converge; no frozen UI |
| 11 | Inspect job | per-operation before/intended/**written** values recorded |
| 12 | Verify in Paperless | titles updated; **all other custom fields intact** (Blocker 1) |
| 13 | Open History later | job + operations fully browsable after restart |
| 14 | Rollback preview | all successful ops listed as revertible |
| 15 | Externally edit one title in Paperless, re-preview | that op is **CONFLICT**; others revertible |
| 16 | Apply rollback | others reverted; the conflicted doc keeps its newer value; rollback recorded as its own job linked to the original |

Steps 7, 12 and 15 are the three that actually prove the MVP thesis; the rest is table stakes.

### K.2 Unit tests (critical logic)

*Filter compiler:* each operator per field type; `is_empty`/`is_not_empty` → the correct OR-pair; `exists`; nested custom-field AND/OR/NOT; `ANY` over one core field → `__id__in`; `NONE` → `__id__none`; date lookups within `DATE_KWARGS`; **rejection** of OR across heterogeneous core fields with a useful reason; depth/atom limits; unknown field names; field-name→id resolution incl. names containing spaces/accents.

*Template:* single/multiple/adjacent placeholders; missing field; present-but-empty value; unknown placeholder (definition-time error); literal `{`/`}` escaping; select → label; monetary → formatted; date formatting; accented and very long names; no-op when output equals current title.

*Transformation:* `SET` on each supported target; `CLEAR` on custom field vs core field; `REPLACE` substring vs whole-value, case sensitivity, no-match → unchanged; multi-op ordering.

*Preview:* counts; error rows; unchanged detection; `not_permitted` from `user_can_change`; `preview_token` stability and mismatch rejection.

*Executor:* idempotent no-op path; `SKIPPED_CONFLICT` when before-value moved; `SKIPPED_PERMISSION` on 403; retry on 5xx/429 with backoff; **no retry** on 400; `written_value` parsed from the response; normalisation recorded when written ≠ intended.

*Rollback:* inverse generation; conflict when current ≠ written; skip already-reverted; handle deleted/trashed document; partial rollback counts.

*Jobs:* atomic claim (no double execution under concurrent claims); `INTERRUPTED` sweep; resume only touches `PENDING`; resume detects an already-applied write and marks it `SUCCEEDED` without re-patching; cancellation mid-flight; unique index prevents duplicate operations.

*Quality:* rule → counting FilterSet; empty-vs-absent semantics; percentage arithmetic incl. zero-document schemas.

### K.3 Integration tests (respx-mocked Paperless)

- **CF preservation regression (Blocker 1):** mock reproduces `drf-writable-nested` replace semantics; assert setting one field preserves the rest; assert a *partial* array would have destroyed them (proving the mock is faithful).
- Version negotiation: `Accept` header on every request; `X-Api-Version` parsing; 406 handling; missing headers → incompatible.
- Pagination iterator: multi-page, `next` following, `page_size` respected, no reliance on `all`.
- Preview proxy: streaming, allowlisted headers, no auth header leakage to the client, upstream 404 → error envelope.
- Dry run performs **zero** non-GET requests.
- Bounded concurrency: never more than N in-flight PATCHes (instrumented mock).
- Token never appears in any response body, log line, or error payload (assert over captured logs).

### K.4 Frontend tests (vitest + RTL)

Grid renders server data without refetching everything on sort; selection persists across pages; filter builder only offers compilable shapes; preview table shows before/after and errors distinctly; Apply disabled when zero changes; job progress updates from mocked SSE; Inspector edit → save → optimistic update → error rollback; keyboard nav; dark mode snapshot.

### K.5 Non-functional

Explorer p95 < 500ms server-side on a 20k-document instance at `page_size=100`; a 1000-operation job never exceeds the configured concurrency and never exhausts connections; memory stays flat during a large job (streaming, not materialising); restart mid-job leaves a consistent DB (fuzz: kill at random points, assert invariants).

---

## L. Questions / blockers

Only genuinely blocking items; everything else I've defaulted per §45's instruction.

**L.1 — Do you have a Paperless-ngx instance I can target, and which version?** 🔴 *Blocking for M1 verification, not for M0.*
Eight items in C.16 need empirical confirmation, most importantly the destructive custom-field PATCH (which drives the whole write path) and monetary/select round-trips. If you can provide a non-production instance (URL + read/write token, ideally with the `Relevé de vacations` type and its two custom fields), I'll verify them for real. Otherwise I'll build against `docker-compose.dev.yml` with a seeded local Paperless and flag anything I couldn't confirm against *your* data. Either way, please confirm your Paperless version so I can validate the v10 assumption — if you're on an older release serving API v9, the pinned version and the `all`/`title_content` deprecations change.

**L.2 — Confirm the write path decision.** 🟡 *Cheap to confirm now, expensive to change in M8.*
Per-document `PATCH` with bounded concurrency (not `bulk_edit`), because `bulk_edit` has no `set_title` and is asynchronous/unverifiable per document. Consequence: a 5,000-document job is 5,000 sequential-ish HTTP requests — minutes, not seconds, with visible progress. I believe correctness and rollback fidelity are worth that, consistent with §39's priority ordering, but it is a user-visible performance trade-off and I want your explicit agreement before building on it.

**L.3 — Licence?** 🔴 *Blocking for M0 (`LICENSE` file + repo metadata).*
"Open-source" is specified but not which licence. Default if you don't care: **GPL-3.0**, matching Paperless-ngx itself and keeping the ecosystem consistent. Say MIT/Apache-2.0 if you'd rather maximise adoption/permissiveness.

Not asking (defaults applied, tell me if you disagree): Vite over Next.js; SSE over WebSockets; Alembic from day one; concurrency default 4; `page_size` default 100; job status enum per B.2 C3; `on_missing="error"` for templates; native iframe PDF preview; English-only UI in MVP with i18n-ready strings (your domain data is French, so I'll ensure accents/UTF-8 are first-class in field names, templates and filters).

---

## Recommended specification changes

### 🔴 Blocking (data safety / correctness — need agreement before M2/M6/M8)

1. **Mandate read-modify-write for all custom-field writes.** PATCH `custom_fields` is a full replacement and deletes omitted fields (C.9). Add to §7/§11 as a hard rule, enforce it in `PaperlessClient`'s public surface, and cover it with a regression test.
2. **Record `written_value`, not just intended value.** `JobOperation` carries `before_value`, `intended_value`, `written_value`; rollback conflict detection compares against `written_value` (B.3 Blocker 2). Amends §13/§15.
3. **Primary write path is per-document `PATCH`, not `bulk_edit`.** `bulk_edit` cannot set titles and is asynchronous (C.11). Amends §11/§13. (See L.2.)
4. **Enforce single-instance execution.** Startup lock + single worker + WAL; otherwise the in-process job engine double-executes writes (B.3 Blocker 3). Amends §13/§H.
5. **Define the compilable FilterSet subset and fail loudly outside it.** No server-side OR across heterogeneous core fields; the compiler raises rather than silently falling back to client-side filtering (B.2 C4). Amends §8, and constrains the §9 filter UI.
6. **Add forward conflict detection between preview and apply.** A stale approved preview must not overwrite a newer edit; introduces `SKIPPED_CONFLICT` (B.3). Amends §12/§13.

### 🟡 Optional improvements (recommended, non-blocking)

7. **Drop Next.js for a Vite SPA** served by FastAPI — simpler single-container deployment, no second server runtime (B.2 C1). Amends §4/§5.
8. **Revise the Job state model** to `PENDING/RUNNING/COMPLETED/PARTIAL/FAILED/CANCELLED/INTERRUPTED` with rollback expressed relationally (B.2 C3). Amends §13.
9. **Fold `expected_metadata` into a typed `rules` list** on Schema (B.2 C6). Amends §16.
10. **Implement M4 (Filter Engine) before/with M3 (Explorer)** so the grid never gets a throwaway filter path (J). Amends §42.
11. **Add a threat-model statement to the README:** no built-in auth, full-write token, private-network only (I.1). Amends §47.
12. **Record the tag-write hazard now** (hierarchy side effects, C.10) so the future `ADD TAG`/`REMOVE TAG` transforms are designed around observed post-write state. Roadmap note for §11.
13. **Use Paperless' existing `document_count` and `duplicate_documents`** rather than building counting/duplicate detection later (B.5). Roadmap note for §21.
14. **Exclude trashed documents from datasets by default** and treat them as clean per-operation failures in jobs (B.3).
15. **Prefer `bulk_edit.modify_custom_fields` post-MVP** as a genuinely merge-style (and faster) custom-field write path, once async completion verification exists (C.11).

Nothing here changes the product scope of §37/§38. Items 1–6 change *how* the MVP writes to Paperless; 7–15 are simplifications and documentation.

---

## Next step

Awaiting your go-ahead on the blocking items (1–6), a yes/no on the optional ones (7–15), and answers to **L.1** (test instance + Paperless version), **L.2** (write-path confirmation) and **L.3** (licence).

On approval I'll start **M0** — repository skeleton, Docker, config, logging, DB/migrations, CI, docs — and report back with tests run and results before moving to M1.
