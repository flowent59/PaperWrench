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

---

## 3. Endpoints PaperWrench uses

| Endpoint | Method | Purpose | Confidence |
| --- | --- | --- | --- |
| `/api/documents/` | GET | list / search / filter, connection probe | `VERIFIED_LIVE` |
| `/api/documents/{id}/` | GET | read a single document | `VERIFIED_LIVE` |
| `/api/documents/{id}/` | PATCH | write title, dates, and custom fields | `VERIFIED_LIVE` |
| `/api/documents/{id}/metadata/` | GET | archive/original metadata | `VERIFIED_LIVE` |
| `/api/custom_fields/` | GET | field catalogue (id, name, data type, select options) | `VERIFIED_LIVE` |
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

Because of this, `PaperlessClient.update_custom_fields()` does not accept a
"partial" mode at all. The unsafe call is not merely discouraged — it is not
expressible through the client.

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

---

## 6. Search, filtering and ordering

**`VERIFIED_LIVE`** — `title_search`, `title__icontains`, `content__icontains`
and the full-text `query` parameter all work, and all compose with pagination.

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

**`ASSUMED`** — Paperless has no ETag / `If-Match` support on documents, so
there is no server-side optimistic locking to lean on. PaperWrench therefore
implements its own forward conflict detection (ADR-0005): the caller passes the
state it based its decision on, and the client refuses to write from a stale
base. This is the reason `update_custom_fields()` takes `expected_before`.

**`ASSUMED`** — concurrent writers (another PaperWrench, the Paperless web UI, a
consumer) can modify a document between our read and our write. The read →
merge → write window is small but non-zero. We narrow it, we do not close it.

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
