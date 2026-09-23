# Paperless-ngx REST API — field notes

This is not a copy of the upstream documentation. It records what PaperWrench
actually depends on, and how sure we are about each item.

Everything below was gathered against **Paperless-ngx 3.1.2** (the version the
project targets first), running as the sandbox stack defined in
`docker-compose.dev.yml` (Paperless 3.1.2 + PostgreSQL 16 + Redis 7).

## Confidence nomenclature

Every claim in this document carries exactly one tag.

| Tag | Meaning |
| --- | --- |
| `VERIFIED_LIVE` | Observed by executing a request against a real Paperless-ngx 3.1.2 instance, and pinned by an automated test in `tests/backend/live/`. This is the only tag that may be used to justify a write-critical behaviour. |
| `VERIFIED_SOURCE` | Read in the Paperless-ngx source code of the running container (`/usr/src/paperless/src/...`), but not exercised end-to-end by our own request. |
| `ASSUMED` | Inferred from documentation, from the shape of the code, or from reasoning. **Not** proof. Anything write-critical must be promoted to `VERIFIED_LIVE` before we rely on it. |

**Rule.** A behaviour that can destroy user data may only be considered
definitively validated once it has been observed on a real Paperless-ngx 3.1.2
instance. Until then it stays `ASSUMED`, and the code must be written as if the
worst plausible interpretation were true.

Live coverage is opt-in and gated; see [Live verification](#live-verification).

---

## 1. Versioning and content negotiation

**`VERIFIED_SOURCE`** — 3.1.2 declares:

```python
# /usr/src/paperless/src/paperless/settings.py
REST_FRAMEWORK = {
    "DEFAULT_VERSION": "10",
    "ALLOWED_VERSIONS": ["9", "10"],
    ...
}
```

**`VERIFIED_LIVE`** — the version is negotiated with the `Accept` header:

```
Accept: application/json; version=10
```

**`VERIFIED_LIVE`** — requesting a version outside `ALLOWED_VERSIONS` (for
example `version=99`) returns **HTTP 406** with a JSON body containing a
`detail` key. Requesting `version=9` succeeds and returns a v9-shaped body.

### The `X-Api-Version` trap

**`VERIFIED_SOURCE`** — the response header is not the negotiated version:

```python
# /usr/src/paperless/src/paperless/middleware.py
response["X-Api-Version"] = ALLOWED_VERSIONS[-1]
```

**`VERIFIED_LIVE`** — a request sent with `Accept: application/json; version=9`
comes back with `X-Api-Version: 10` **and** a v9 body (the `all` key is
present). `X-Api-Version` is therefore the **highest version the server
supports**, never the version that was actually served.

Consequence for us: a naive "is the negotiated version what I asked for?" check
built on `X-Api-Version` is meaningless — it would compare our requested version
against the server maximum. The only trustworthy compatibility signal is the
**status code**: 406 means "this server cannot serve the version PaperWrench
speaks". `PaperlessClient.check_connection()` is written accordingly, and
`ConnectionStatus.api_version` is documented as *the highest supported version*,
not the negotiated one.

`X-Version` carries the Paperless-ngx release string (`3.1.2`). **`VERIFIED_LIVE`**

### v9 → v10 difference that matters to us

**`VERIFIED_LIVE`** — in v9 a paginated list response carries an extra `all`
key holding *every* matching id. In v10 that key is gone. PaperWrench must never
depend on `all`; it walks pages instead. A live test asserts both halves of this
(absent in v10, present in v9) so a regression is caught immediately.

---

## 2. Authentication

**`VERIFIED_LIVE`** — token auth via `Authorization: Token <token>`.

**`VERIFIED_LIVE`** — an invalid token returns **HTTP 401** with
`{"detail": "Invalid token."}`.

**`VERIFIED_LIVE`** — `GET /api/profile/` returns the caller's profile and it
**includes the API token in clear text** (`auth_token`). PaperWrench does not
call this endpoint, and must never proxy it, log it, or store its payload.

The token is a secret for the whole of PaperWrench: it is registered with the
log scrubber at client construction, and both `PaperWrenchError` and
`PaperlessApiError` scrub their message and details before they can reach the
browser. Upstream response bodies are scrubbed **before** truncation, so a token
straddling the truncation boundary cannot survive as a recognisable fragment.

### 2.1 401 vs 403 (M2)

**`VERIFIED_LIVE`** — a token that is rejected outright (bad/unknown token)
returns **HTTP 401** with `{"detail": "Invalid token."}`.

**`VERIFIED_LIVE`** — a token that Paperless *accepts* as valid, but that
belongs to a user with **zero permissions**, returns **HTTP 403** with
`{"detail": "You do not have permission to perform this action."}` on both
`GET /api/documents/` (list) and `GET /api/documents/{id}/` (detail). This was
proven with a disposable sandbox user created and deleted per test
(`restricted_user` fixture in `tests/backend/live/conftest.py`), never a
hardcoded account.

Consequence: 401 and 403 are a genuine authentication/authorization split, not
one error class wearing two status codes. PaperWrench models them separately —
`PaperlessUnauthorizedError` (401) and `PaperlessForbiddenError` (403), both
non-retryable — so a caller (and a future permissions UI) can tell "your
credential is wrong" from "your credential is fine, but you may not do this"
without inspecting response text.

**`VERIFIED_LIVE` — a sharper nuance:** granting a user the *global*
`view_document` permission, **without** an object-level grant for a specific
document, does not surface as 403 for that document. It surfaces as **404**:
the document is simply absent from the list, and `GET` on its id returns
`{"detail": "Not found."}`. Paperless's object-level permission model makes an
ungranted object indistinguishable from a nonexistent one at this boundary —
PaperWrench must not assume "404 on a document id I previously read" always
means the document was deleted; it can also mean a permission was revoked.

---

## 3. Endpoints PaperWrench uses

| Endpoint | Method | Purpose | Confidence |
| --- | --- | --- | --- |
| `/api/documents/` | GET | list / search / filter, connection probe | `VERIFIED_LIVE` |
| `/api/documents/{id}/` | GET | read a single document | `VERIFIED_LIVE` |
| `/api/documents/{id}/` | PATCH | write title, dates, and custom fields | `VERIFIED_LIVE` |
| `/api/documents/{id}/metadata/` | GET | archive/original metadata | `VERIFIED_LIVE` |
| `/api/custom_fields/` | GET | field catalogue (id, name, data type, select options) | `VERIFIED_LIVE` |
| `/api/tags/` | GET | tag catalogue | `VERIFIED_LIVE` |
| `/api/correspondents/` | GET | correspondent catalogue | `VERIFIED_LIVE` |
| `/api/document_types/` | GET | document type catalogue | `VERIFIED_LIVE` |
| `/api/storage_paths/` | GET | storage path catalogue | `VERIFIED_LIVE` |
| `/api/documents/post_document/` | POST | used **only** by the dev seeder, never by the app | `VERIFIED_LIVE` |

**`VERIFIED_LIVE`** — `GET /api/` returns **HTTP 302** (redirect to the
browsable schema view), so it is unusable as a health probe. The client probes
`/api/documents/?page_size=1` instead, and runs with
`follow_redirects=False` so that an unexpected redirect surfaces as an error
rather than silently landing somewhere else.

---

## 4. Pagination

**`VERIFIED_LIVE`** — list responses are:

```json
{"count": 17, "next": "http://.../api/documents/?page=2", "previous": null, "results": [...]}
```

- `page` and `page_size` are query parameters. **`VERIFIED_LIVE`**
- `page_size` is capped server-side; PaperWrench clamps its own requests to 250. **`ASSUMED`** (the clamp is ours; the exact server maximum was not probed)
- **`VERIFIED_LIVE`** — an out-of-range `page` (e.g. `page=99999`) returns **HTTP 404** with `{"detail": "Invalid page."}`, not an empty page. Any paging loop must treat 404 as "stop", not as "the document vanished".

`PaperlessClient.iter_pages()` walks by **page number**, not by following the
`next` URL. The `next` URL is built from the server's own idea of its hostname,
which behind a reverse proxy can point somewhere PaperWrench cannot reach.

---

## 5. Custom fields — the dangerous part

### 5.1 The hazard, now confirmed

**`VERIFIED_SOURCE`** — `DocumentSerializer` inherits
`drf_writable_nested.NestedUpdateMixin`, which treats a nested collection as a
**full replacement**, not a merge.

**`VERIFIED_LIVE` — this is the single most important fact in this document.**

The experiment: a document carrying **5** custom field values received a PATCH
containing **1** custom field value.

```
PATCH /api/documents/{id}/   {"custom_fields": [{"field": 3, "value": "..."}]}
→ HTTP 200
→ document now has 1 custom field value
→ 4 custom field values were silently DELETED
```

There is no warning, no 4xx, no partial-update semantics. **HTTP 200 and four
destroyed values.** This is exactly the data-loss scenario ADR-0004 was written
to prevent, and it is now empirically proven rather than merely feared.

**`VERIFIED_LIVE`** — the mitigation works: reading the complete
`custom_fields` array, merging the single change into it by field id, and
PATCHing the **complete** array back preserves all 5 values. This is
`merge_custom_fields()` in `paperless/models.py`, and it is the only supported
way for PaperWrench to write a custom field.

M5 makes this boundary enforceable (#7): `update_document()` accepts only a
closed core-field allowlist and rejects `custom_fields` and every unknown key
before I/O. `mutate_document()` merges operations into the complete fresh state
under the shared per-document lock, supporting one combined core/custom PATCH.
The compatibility custom helper uses that lock too. Both custom write paths
require explicit external-race acknowledgement (ADR-0012).

### 5.2 Data types and round-trips

**`VERIFIED_LIVE`** for every row: value written, then re-read, then compared.

| Data type | Wire representation | Notes |
| --- | --- | --- |
| `string` | JSON string | French accents round-trip byte-for-byte (`Relevé de vacations — août`) |
| `integer` | JSON number | |
| `float` | JSON number | |
| `boolean` | JSON `true` / `false` | `false` is a legitimate value, not "empty" |
| `date` | `"YYYY-MM-DD"` | |
| `monetary` | `"EUR1234.56"` | currency prefix + **dot** decimal separator |
| `select` | opaque option **id** string | not the human label |
| `url`, `documentlink` | JSON string / list | not exercised beyond read |

**`VERIFIED_LIVE`** — a monetary value using a comma decimal separator
(`"EUR1234,56"`, the natural French form) is **rejected with HTTP 400**, and the
rejection is **atomic**: the document is left completely untouched, including
the other custom fields in the same payload. This matters, because the naive
French input is the one a user will type.

**`VERIFIED_LIVE`** — `"EUR0.00"` is a perfectly valid stored value and is
**not** the same thing as an absent field. Empty string `""` and `null` are also
storable and distinct from absence. Any "is this field filled in?" logic must
distinguish *absent* from *falsy*; the unit tests pin this with
`["", None, False, 0, "EUR0.00"]`.

**`VERIFIED_LIVE`** — writing a select field with its human label instead of its
option id is rejected with HTTP 400.

**`VERIFIED_LIVE`** — referencing an unknown custom field id returns HTTP 400
and leaves the document unmodified.

### 5.3 Custom field *definitions* — `extra_data` can be `null` (M2)

**`VERIFIED_LIVE`** — on `GET /api/custom_fields/`, the `extra_data` key on a
field definition is **`null`**, not an absent key and not `{}`, for every
`string`, `date`, `boolean`, `integer` and `float` field observed in the
Golden Dataset (10 of 13 real fields). Only `select` (which carries its
`select_options` there) and `monetary` (which carries `default_currency`)
happened to have a non-null `extra_data` in the sandbox — which is exactly why
this was not caught by the mocked tests written during M1: they always
supplied a dict literal for `extra_data`, never `null`.

This was found running the M2 metadata endpoints against the real sandbox for
the first time: `CustomField.model_validate(...)` raised a pydantic
`ValidationError` (`extra_data: Input should be a valid dictionary`) on every
field of the affected types, which the API surfaced as an unhandled
`HTTP 500`. Fixed with a `field_validator(mode="before")` on `CustomField`
that normalises `None` → `{}` before the rest of validation runs, so
`select_options` and `typed_value()` can keep assuming a dict. Pinned by a
unit test (`TestCustomFieldExtraDataNull`), a mocked test
(`TestListCustomFields`), and a live test
(`TestReferenceMetadataReads.test_list_custom_fields_handles_the_real_null_extra_data`).

### 5.4 Concurrent writes to different fields — a measured, not fixed, hazard (M2)

**`VERIFIED_LIVE`** — the read-modify-write mitigation in §5.1 closes the
*omitted-field-deletion* hazard, but it does **not** close the classic
read/read/write/write lost-update race. Two actors, each reading the
document's current custom fields and then PATCHing back the **full** list
they believe is correct with only their own field changed, can lose one
actor's change even though the two actors never touched the same field:
whichever actor's PATCH lands second re-asserts the value it read *before*
the other actor's write, silently reverting it.

This was measured directly by `TestConcurrentCustomFieldWrites` in M2. M5 adds
`test_inspector_live.py` with event-controlled interleaving, not timing luck.

**VERIFIED_LIVE (M5, 3.1.2):** cooperating PaperWrench mutations sharing a
coordinator serialize their entire read/check/merge/PATCH cycle. A queued mutation
with an old revision gets a non-retryable 409 and no PATCH. Low-level fresh merges
without a revision still preserve both cooperating actors' unrelated changes.

**VERIFIED_LIVE (M5, 3.1.2):** an external actor forced to write after the local
GET and before the local PATCH still loses its unrelated custom-field change.
There is no external atomicity. `If-Match: "impossible-etag-m5"` together with
`If-Unmodified-Since: Thu, 01 Jan 1970 00:00:00 GMT` still yields 200 and applies
the title PATCH. These headers are not a usable compare-and-swap contract.

ADR-0012 selects local locks, conservative stale-document/catalogue rejection,
and explicit per-save risk acknowledgement. It does not claim to fix the external
race. No distributed lock service is introduced; pausing external writers is an
operator action and remains ASSUMED. There is no durable rollback in M5.

---

## 6. Search, filtering and ordering

**`VERIFIED_LIVE`** — `title_search`, `title__icontains`, `content__icontains`
and the full-text `query` parameter all work, and all compose with pagination.

### Search is not filtering, and `search` is not one thing

**`VERIFIED_SOURCE`** (`documents/views.py`, `_TANTIVY_SEARCH_PARAM_NAMES`) —
there are **four** search parameters, they are mutually exclusive (more than
one is a 400), and they search different things:

| Parameter | Searches | PaperWrench `SearchSpec` mode |
| --- | --- | --- |
| `title_search` | the title, via the full-text index | `title` |
| `text` | the extracted document text | `content` |
| `query` | the whole index, in raw Tantivy syntax | `advanced` |
| `more_like_id` | similarity to another document | *not modelled* |

**These are not field lookups.** When one is present, `DocumentViewSet.list`
leaves the ordinary queryset path entirely: it queries a **Tantivy index**,
gets a ranked list of ids back, and intersects those with the ORM-filtered
queryset. Filters narrow a queryset; search produces ids from a different
engine. That they intersect is an implementation convenience, not evidence
that they are the same kind of thing.

Consequence, and the reason this is spelled out here: **M3's single `search`
parameter always meant `title_search`** and documented that nowhere, so
"search" silently meant something much narrower than a user would assume.
M4 replaced it with an explicit `SearchSpec {mode, text}`; `advanced` is
passed through opaquely and PaperWrench claims nothing about Tantivy syntax.
Search is therefore **not** part of a FilterSet — see **ADR-0010**.

**`VERIFIED_LIVE` — and this is a trap for the future filter engine:** an
**unknown filter parameter is silently ignored**. `?not_a_real_filter=42`
returns the full unfiltered result set with HTTP 200. An unknown `ordering`
value is likewise silently ignored.

Consequence: PaperWrench can never treat "Paperless accepted my filter" as
"Paperless applied my filter". The M4 filter engine must validate every filter
key against a known-good allowlist **before** sending it, otherwise a typo in a
saved filter turns "these 12 documents" into "the entire library" — while
looking successful. This is now a hard requirement on ADR-0007's compilable
subset.

---

## 6.1 Reference metadata endpoints (M2)

| Endpoint | Method | Purpose | Confidence |
| --- | --- | --- | --- |
| `/api/tags/` | GET | tag catalogue | `VERIFIED_LIVE` |
| `/api/correspondents/` | GET | correspondent catalogue | `VERIFIED_LIVE` |
| `/api/document_types/` | GET | document type catalogue | `VERIFIED_LIVE` |
| `/api/storage_paths/` | GET | storage path catalogue | `VERIFIED_LIVE` |

**`VERIFIED_LIVE`** — full key set on a freshly created object of each kind:

- Tag: `id, slug, name, color, text_color, match, matching_algorithm, is_insensitive, is_inbox_tag, owner, user_can_change, parent, children`
- Correspondent: `id, slug, name, match, matching_algorithm, is_insensitive, owner, user_can_change`
- Document type: `id, slug, name, match, matching_algorithm, is_insensitive, document_count, owner, user_can_change`
- Storage path: `id, slug, name, path, match, matching_algorithm, is_insensitive, owner, user_can_change`

PaperWrench's `Tag`, `Correspondent`, `DocumentType`, `StoragePath` models
(`paperless/models.py`) keep only the subset the project actually consumes
(`extra="ignore"` on every one), so an upstream field addition is a no-op
here rather than a validation break.

**`VERIFIED_LIVE`** — Paperless enforces a per-owner **uniqueness constraint**
on tag and custom field names: two objects owned by the same user cannot
share an exact name. This does **not** mean names are globally unique across
owners, and PaperWrench's `MetadataRegistry` does not rely on the Paperless
constraint at all — it detects ambiguity itself (`AmbiguousMetadataName`) by
scanning its own snapshot for an exact-name collision, so it stays correct
even in a hypothetical multi-owner future. Name resolution is deliberately
**exact-match only**: no accent-folding, no case-insensitivity. The Golden
Dataset deliberately contains near-duplicate names that must **not** be
conflated (`Etablissement` / `Établissement`, `Periode concernee` /
`Période concernée`, `Reference interne` / `Référence interne`, `Valide` /
`Validé`) — these are two distinct custom fields each, and a normalising
resolver would silently merge them.

---

## 6.2 Ordering, including `custom_field_<id>` (M3)

**`VERIFIED_SOURCE`** (Paperless-ngx 3.1.2, `src/documents/views.py`,
`DocumentViewSet.ordering_fields`) — the server's ordering allowlist is:

```
id, title, correspondent__name, document_type__name, storage_path__name,
created, modified, added, archive_serial_number, num_notes, owner,
page_count, custom_field_<id>
```

PaperWrench's own `GET /api/v1/documents` (`backend/src/paperwrench/api/v1/
documents.py`) exposes a **deliberately smaller** subset of this — `title`,
`created`, `modified`, `added`, `archive_serial_number`, `correspondent`
(→ `correspondent__name`), `document_type` (→ `document_type__name`) — plus
`custom_field_<id>` for known custom fields. `num_notes`, `owner`,
`page_count` and `storage_path__name` are not (yet) Explorer columns and are
intentionally left off the allowlist rather than exposed "because the server
accepts them" (M3 brief).

**`VERIFIED_SOURCE`** (`src/documents/filters.py`, `DocumentsOrderingFilter`)
— `custom_field_<id>` ordering is implemented per data type (STRING/
LONG_TEXT via `value_text`, INT via `value_int`, FLOAT via `value_float`,
DATE via `value_date`, MONETARY via `value_monetary_amount`, SELECT via a
`Case`/`When` on the option list, DOCUMENTLINK via `value_document_ids`, URL
via `value_url`, BOOL via `value_bool`). Every one of these first annotates
`has_field` (`Exists(...)`) and orders by `-has_field`, so **documents that
have the field always sort before documents that do not**, regardless of
ascending/descending direction on the value itself.

**`VERIFIED_LIVE`** (`tests/backend/live/test_paperless_live.py::
TestCustomFieldOrderingLive`, run against the Golden Dataset) confirms, for
the three data types M3 actually exposes:

- **String** (`Période concernée`): ascending and descending both return
  200 with the same document set, in different orders.
- **Monetary** (`Montant`, deliberately PRESENT/absent/zero on different
  Golden Dataset rows): ordering succeeds without error and without
  dropping any document — the ABSENT/zero distinction Paperless itself
  makes (has-field-first) is exactly the one PaperWrench's `CustomFieldValue
  Kind` already models.
- **Date** (`Date de règlement`): ordering succeeds.
- The has-field-first invariant (documents WITH the field never appear
  after documents WITHOUT it) holds for `Montant`.
- Boolean (`Validé`) ordering also works upstream (VERIFIED_LIVE) — proving
  Paperless supports more types than PaperWrench exposes. PaperWrench does
  **not** expose ordering for boolean/select/int/float/documentlink/url
  custom fields in M3: the brief only asked for Text/Monetary/Date, and
  extending the allowlist to a type that was not asked for is exactly the
  kind of premature scope creep M3 was told to avoid. Revisit this in a
  later milestone if a concrete need for it appears.

**`VERIFIED_LIVE`** (unchanged from M1, re-confirmed): an **unknown**
`ordering` value is silently ignored by Paperless rather than rejected —
this is precisely why PaperWrench's own `resolve_ordering()` rejects
anything not on its allowlist **before** ever building the outgoing
request, with a 422 (`InvalidOrderingError`). A caller-supplied ordering
value is never forwarded to Paperless unless PaperWrench itself has already
proven it maps to something real.

**`VERIFIED_LIVE`** (`TestSearchPaginationOrderingComposeLive`) — `search`
(`title_search`)/`query`, `ordering` and pagination (`page`/`page_size`)
compose correctly together against the Golden Dataset: results stay within
the requested page size, distinct pages do not overlap, and adding a filter
alongside search + ordering does not silently drop either constraint. M1
had only proven these individually; M3 required (and found) no interaction
defect when combined.

---

## 6.3 Soft-deleted documents are excluded by default (M3)

**`VERIFIED_SOURCE`** — `Document` (`src/documents/models.py`) is a
`SoftDeleteModel` (via `django-softdelete`). Reading that package's source
directly (`softdelete/models.py`, `SoftDeleteManager.get_queryset()`)
confirms its default `objects` manager already filters
`deleted_at__isnull=True`. `DocumentViewSet`'s default queryset uses this
manager, so **Paperless's ordinary list (`GET /api/documents/`) and detail
(`GET /api/documents/<id>/`) endpoints already exclude trashed documents by
construction** — this is not something PaperWrench needs to filter for
itself, and there is no separate "trash" flag to additionally check on the
document payload.

Consequence for M3: the Explorer needs no client-side or server-side filter
for `deleted_at`, and no trash UI — a document that has been moved to
Paperless's trash simply stops appearing, exactly like a document that was
permanently deleted. Retrieving/restoring trashed documents (Paperless's
`/api/trash/` endpoint) is out of scope for M3 and is not modelled at all
yet.

---

## 6.4 Filtering — the surface the M4 Filter Engine compiles to

**`VERIFIED_SOURCE`** (`src/documents/filters.py`, `DocumentFilterSet`) — the
lookups PaperWrench's compiler is built on. The generated names come from
`Meta.fields` plus the explicitly declared filters; note that django-filter
**strips a trailing `__exact`**, so `correspondent__id__exact` is exposed as
`correspondent__id`.

| Field | Lookups | Notes |
| --- | --- | --- |
| `title` | `istartswith`, `iendswith`, `icontains`, `iexact` | `CHAR_KWARGS`. **No case-sensitive exact match exists.** |
| `archive_serial_number` | `exact`, `gt`, `gte`, `lt`, `lte`, `isnull` | `INT_KWARGS` |
| `created` | `year`, `month`, `day`, `gt`, `gte`, `lt`, `lte` | `DATE_KWARGS`. **No `exact`.** `created` is a `DateField`. |
| `added`, `modified` | `year`, `month`, `day`, `gt`, `gte`, `lt`, `lte`, `date__gt`, `date__gte`, `date__lt`, `date__lte` | `DATETIME_KWARGS`. Both are `DateTimeField`s. |
| `correspondent`, `document_type`, `storage_path` | `isnull`; `__id`, `__id__in`, `__id__none`; `__name` char lookups | `__id__none` is a declared `ObjectFilter(exclude=True)` |
| `tags` | `tags__id__all`, `tags__id__in`, `tags__id__none`, `is_tagged` | see below |
| custom fields | `custom_field_query`, plus the deprecated `custom_fields__icontains` | see §6.5 |

**`VERIFIED_SOURCE` + `VERIFIED_LIVE`** — the **tag semantics**, which are
three different questions and are easy to get quietly wrong. `ObjectFilter`:

```python
if self.in_list:                             # tags__id__in
    qs = qs.filter(tags__id__in=object_ids).distinct()     # has ANY of
else:
    for obj_id in object_ids:
        if self.exclude:                     # tags__id__none
            qs = qs.exclude(tags__id=obj_id)               # has NONE of
        else:                                # tags__id__all
            qs = qs.filter(tags__id=obj_id)                # has ALL of
```

Confirmed live (`TestFilterEngineTagsLive`) on a document carrying exactly one
of two tags. PaperWrench exposes these as `has_all_of` / `has_any_of` /
`has_none_of` and never simulates any of them. `is_tagged` is a `BooleanFilter`
on `tags__isnull` with `exclude=True`, i.e. "has at least one tag".

A consequence for the compiler: two `has_all_of` conditions can be **merged**
into one parameter over the union of their ids (the loop ANDs them anyway),
but two `has_any_of` conditions **cannot** — "any of {A,B} and any of {C,D}"
is not "any of {A,B,C,D}", and merging would silently widen the filter. Same
for `tags__id__none`, which merges (each id is a separate `.exclude()`).

**`VERIFIED_LIVE`** — filter parameters **compose with AND** and nothing else.
Django filter backends narrow the queryset in turn; there is no union form. So
"title contains X OR correspondent is Y" has no server-side expression, which
is why PaperWrench refuses it (`CORE_OR_UNSUPPORTED`) rather than
approximating it. See ADR-0007.

**`VERIFIED_LIVE`** — filters compose with full-text search too, by
intersection. VERIFIED_SOURCE for the mechanism: when a search parameter is
present, `DocumentViewSet.list` computes `filtered_qs =
self.filter_queryset(self.get_queryset())` and intersects the Tantivy hit ids
with it. Confirmed live including with a `custom_field_query` in the mix
(`TestFilterEngineCompositionLive`), which matters because custom fields are
evaluated through a separate annotated subquery.

### An empty filter value is silently DROPPED

**`VERIFIED_LIVE`** — `?title__icontains=` returns the **entire library**, not
the documents with an empty title.

This is django-filter, not Paperless: `Filter.filter()` starts with

```python
if value in EMPTY_VALUES:      # ([], (), {}, '', None)
    return qs
```

so a filter whose value is the empty string is skipped altogether and the
queryset comes back untouched. Nothing in the response distinguishes this from
a filter that matched everything.

This is the same failure family as the silently-ignored *unknown* parameter in
§6, and it is arguably worse, because the parameter name is perfectly valid.
Consequence for PaperWrench: the Filter Engine **refuses an empty text value at
validation time** rather than emitting one (`VALUE_EMPTY`), and points the
user at `is_empty` / `is_missing`, which are real, server-side questions. Pinned
by `TestFilterEngineServerLimitsLive::test_an_empty_filter_value_is_silently_ignored_by_django_filter`.

---

## 6.5 `custom_field_query` — the nested boolean expression language

**`VERIFIED_SOURCE`** (`CustomFieldQueryParser`) — a single query parameter
carrying JSON. Three forms:

```
[<field id or name>, <operator>, <value>]   an atom
["AND" | "OR", [expr, expr, ...]]           n-ary
["NOT", expr]                                negation
```

Limits: **depth ≤ 10**, **≤ 20 atoms** (`CUSTOM_FIELD_QUERY_MAX_DEPTH`,
`CUSTOM_FIELD_QUERY_MAX_ATOMS`). Depth counts every expression node, atoms
included, so an atom is depth 1. Exceeding either is a 400 (`VERIFIED_LIVE`).
PaperWrench enforces both itself so an over-complex filter is refused locally
rather than costing a round trip.

**`VERIFIED_SOURCE`** — operators are gated by data type
(`SUPPORTED_EXPR_CATEGORIES` × `EXPR_BY_CATEGORY`):

| Data type | basic (`exact`, `in`, `isnull`, `exists`) | string (`icontains`, `istartswith`, `iendswith`) | arithmetic (`gt`, `gte`, `lt`, `lte`, `range`) | containment (`contains`) |
| --- | :-: | :-: | :-: | :-: |
| `string`, `longtext`, `url` | ✅ | ✅ | — | — |
| `monetary` | ✅ | ✅ | ✅ | — |
| `date` | ✅ | — | ✅ (+ `year__`/`month__`/… prefixes) | — |
| `int`, `float` | ✅ | — | ✅ | — |
| `bool`, `select` | ✅ | — | — | — |
| `documentlink` | ✅ | — | — | ✅ |

`icontains` on a Boolean is a **400** (`VERIFIED_LIVE`), not a silently-ignored
parameter — a welcome exception to §6's rule, though PaperWrench does not rely
on it and refuses such a pair itself before building a request.

**`VERIFIED_LIVE`** — an unknown custom field **id** in a `custom_field_query`
is also a 400 (`{name!r} is not a valid custom field`). Again: useful, not
relied upon. A filter that referenced a deleted field would otherwise be a
silent widening, so PaperWrench validates against its own Metadata Registry
snapshot first.

### `exists` vs `isnull` — why ABSENT and NULL stay separable

**`VERIFIED_SOURCE`, then `VERIFIED_LIVE`.** `exists` counts *field instances*:

```python
annotation = Count("custom_fields", filter=Q(custom_fields__field=custom_field))
```

Every other operator counts instances that also satisfy the value condition:

```python
field_filter = has_field & Q(**{f"custom_fields__{value_field}__{op}": value})
```

So **`isnull=true` means "the field is attached AND its value is null"** and
can never match a document that does not carry the field at all. That
asymmetry is what makes the M4 empty/missing operators possible; see
**ADR-0011** for the full table. Confirmed live by walking one document
through ABSENT → NULL → `""` → a real value
(`TestFilterEngineEmptyMissingLive`).

`exact: ""` is accepted for `CharField`-backed types only — the parser sets
`allow_blank = True` for them explicitly (upstream issue #7361). On a
monetary/date/int/bool/select field it is a 400, which is why PaperWrench only
offers `is_empty` on Text, Long text and URL.

### Monetary comparison ignores the currency code

**`VERIFIED_SOURCE`** — arithmetic and `exact`/`in` on a monetary field compare
against `value_monetary_amount`, a **generated column** that strips a leading
three-character currency code:

```python
value_monetary_amount = models.GeneratedField(
    expression=Case(
        models.When(value_monetary__regex=r"^\d+", then=Cast(Substr("value_monetary", 1), ...)),
        default=Cast(Substr("value_monetary", 4), ...),          # drop "EUR"
        output_field=models.DecimalField(decimal_places=2, max_digits=65),
    ),
    db_persist=True,
)
```

`MonetaryAmountField` applies the same heuristic to the *filter* value: "if it
does not start with a digit or a minus, drop three characters".

Two consequences PaperWrench acts on:

1. **`EUR10.00` and `USD10.00` compare equal.** There is no currency-aware
   comparison available. This is surfaced as a note on the operator in
   `/filters/capabilities` rather than hidden.
2. **PaperWrench sends a bare, normalised decimal string** (`0.00`, not
   `EUR0.00`), so there is no prefix for the heuristic to guess at. The value
   travels as a string end to end and is parsed with `Decimal`; no float is
   ever constructed. `EUR0.00` therefore stays an exact, comparable, non-null
   zero — distinct from ABSENT, as the Golden Dataset's deliberate zero rows
   confirm live.

String lookups on a monetary field run against the raw `value_monetary`
string instead (currency prefix included). PaperWrench does not expose them:
comparing an amount as text matches on digits of unrelated magnitude.

### Select accepts a label where an id belongs

**`VERIFIED_SOURCE` + `VERIFIED_LIVE`** — `SelectField.to_internal_value`
tries to resolve the supplied value as an option **label** first, falling back
to treating it as an id:

```python
data = next(option.get("id") for option in self._options if option.get("label") == data)
```

Convenient, and a trap for anything that stores filters: a filter written with
a label would silently retarget the day someone renames the option.
PaperWrench only ever compiles the stored option id, and its validator rejects
a value that is not one of the field's known option ids — which also rejects a
label. See ADR-0011's sibling rule in `filters/validation.py`.

---

## 7. Other observed behaviours

**`VERIFIED_LIVE`** — Paperless **trims leading and trailing whitespace on
document titles**. PATCHing `"   test   trim   "` stores `"test   trim"`
(interior runs of spaces are preserved, edges are not).

Consequence for the M3 rename engine: after a write, "what I asked for" and
"what is stored" can legitimately differ. A verification step that compares them
literally will report a false failure on every title with edge whitespace. The
comparison must be made against the **normalised** form. The dev seeder already
hit this bug and created a duplicate document because of it.

**`VERIFIED_LIVE`** — `GET` on a non-existent document id returns HTTP 404 with
a `detail` key.

**`VERIFIED_LIVE`** — an unreachable host surfaces as a transport error, which
the client normalises to `PaperlessUnreachableError`; it never leaks an
`httpx` exception to the caller.

**VERIFIED_LIVE (M5):** the tested conditional headers do not prevent writes
on 3.1.2, and forced external interleaving loses an update (see §5.4). Local
preconditions detect stale state before PATCH, not a change inside the external
GET/PATCH race window.

---

## 8. Live verification

The claims tagged `VERIFIED_LIVE` are pinned by `tests/backend/live/`, which is
**deselected by default**. Running it requires both:

1. `PAPERWRENCH_ALLOW_LIVE_TESTS=true` — an explicit opt-in, and
2. a target URL whose **host** is in the authorised allowlist (`127.0.0.1`,
   `localhost`, `::1`, the compose service name, `host.docker.internal`).

The target is read from `PAPERWRENCH_LIVE_PAPERLESS_URL`, falling back to
`PAPERWRENCH_PAPERLESS_URL`. The allowlist compares **exact hostnames**, never
substrings: `paperless.someones-real-domain.example` contains an authorised
name and must still be refused.

> A defect was found in this gate during M1, while testing the gate itself.
> Only the `..._LIVE_...` spelling was read, so exporting
> `PAPERWRENCH_PAPERLESS_URL` had no effect at all: the suite silently fell
> back to its localhost default, ran against an instance the operator had
> never named, and reported **46 passed**. A destructive suite that ignores
> the target it was given — and says nothing — is worse than one that refuses
> to start. Both spellings are now honoured, and the gate has its own unit
> tests in `tests/backend/unit/test_live_gate.py` which run in the default
> suite on every CI job.

Both gates must pass. This is deliberate: the live suite **writes and deletes**,
and it must be impossible for it to run against a real personal library by
accident. A further guard refuses to run if the target instance holds more than
500 documents — a crude but effective "this is not a sandbox" detector.

Reproduce the environment with:

```sh
make dev-paperless-up      # Paperless 3.1.2 + PostgreSQL + Redis on :8010
make dev-paperless-golden  # 17 deliberately imperfect documents, 7 custom fields
make test-live             # opt-in live suite
```

The three backend suites are reported separately and must never be conflated:

```sh
make test-unit    # no I/O whatsoever
make test-mocked  # respx; proves our client's behaviour, NOT Paperless's
make test-live    # the only suite that can promote a claim to VERIFIED_LIVE
```

A mocked test asserts what we believe Paperless does. Only a live test can
tell us whether that belief is true.

## 9. M5 Inspector write verification

`tests/backend/live/test_inspector_live.py` runs the normalized Inspector API
against real REST responses. VERIFIED_LIVE on 3.1.2: `Relevé de vacations` type
resolution; `Période concernée` edits preserving `Montant` and every unrelated
value; title edge-whitespace normalization; monetary and select-ID round trips;
text empty/null/absent transitions; stale-state rejection; view-only
`user_can_change=false` preflight with zero PATCHes; distinct 401/403 and
permission-hidden 404; local serialization and external loss as described above.

The response contains normalized `before` and `document` (actual PATCH result),
plus the exact intended payload. UI cache invalidation and DTO/client rejection
are VERIFIED_SOURCE by automated local tests, not claims about Paperless.
M5's unsupported custom types (float, URL, document link) stay read-only.
