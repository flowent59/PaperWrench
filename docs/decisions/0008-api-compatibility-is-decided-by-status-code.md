# ADR-0008: API compatibility is decided by the status code, not by `X-Api-Version`

## Status

Accepted (M1).

## Context

PaperWrench speaks Paperless REST API v10. Before doing anything else it must
answer a simple question: *can this server actually serve v10?* If it cannot,
every subsequent assumption in the application — the shape of a paginated
response, the semantics of `custom_fields` — is unsafe, and the honest move is
to refuse to operate rather than to corrupt data.

The design written during M0 assumed the obvious mechanism. Paperless returns an
`X-Api-Version` response header; compare it to the version we requested; if they
differ, the server did not give us what we asked for, so declare incompatibility.

That assumption is wrong, and the M1 live tests caught it.

Reading the running 3.1.2 container's own source:

```python
# /usr/src/paperless/src/paperless/middleware.py
response["X-Api-Version"] = ALLOWED_VERSIONS[-1]
```

`X-Api-Version` is a **static advertisement of the highest version the server
supports**. It is set by middleware on every response, unconditionally, without
any reference to what the request negotiated. Confirmed live: a request sent
with `Accept: application/json; version=9` returns `X-Api-Version: 10` together
with an unmistakably v9 body (the v9-only `all` key is present).

Had we shipped the M0 design, the comparison would have been "requested 10,
header says 10" — accidentally correct today, and accidentally correct for the
wrong reason. On any future Paperless that supports v11, a PaperWrench asking
for v10 would read `X-Api-Version: 11`, conclude "the server did not serve my
version", and declare a perfectly healthy instance incompatible. The check would
have failed precisely when it was supposed to help.

The actual negotiation signal is elsewhere. Django REST Framework rejects a
version outside `ALLOWED_VERSIONS` with **HTTP 406 Not Acceptable**, verified
live. A server that returns anything other than 406 for our `Accept` header has
accepted our version.

## Decision

Compatibility is determined by the **status code of a real request**, never by
a response header.

- `PaperlessClient` sends `Accept: application/json; version=10` on every
  request and maps **HTTP 406** to `PaperlessIncompatibleError`. This is the
  authoritative signal, and it is evaluated on every call, not only at startup.
- `check_connection()` performs an actual probe request
  (`GET /api/documents/?page_size=1`) and reports the outcome of that request.
- `X-Api-Version` is still surfaced, but it is named and documented for what it
  is: `ConnectionStatus.api_version` is *the highest version the server
  supports*. It is used only for a **one-directional** sanity check — if the
  advertised maximum is numerically **lower** than the version we speak, the
  server is definitely too old and we say so. A higher advertised maximum is
  never treated as a problem.
- `X-Version` (the Paperless release string, e.g. `3.1.2`) is informational and
  is never used to gate behaviour.

## Consequences

- The compatibility check is forward-compatible: a Paperless supporting v11
  while still allowing v10 is correctly reported as compatible.
- The check costs one real HTTP request. That is acceptable: it is the same
  request we need for the connection probe anyway, and a compatibility verdict
  derived from a header we never validated is worth nothing.
- Every code path already gets the check for free, because 406 is mapped in the
  central `_raise_for_status()`. There is no window in which a request bypasses
  version validation.
- The trap is pinned by a live test whose name states the finding outright,
  `test_x_api_version_is_the_MAXIMUM_not_the_negotiated_version`, so that a
  future contributor who "fixes" the check back to the intuitive version breaks
  a test that explains why they are wrong.

## Alternatives considered

**Trust `X-Api-Version`.** The original design. Rejected: verified to be a
static maximum, not a negotiation result. It would misfire on exactly the
upgrade scenario it was meant to protect.

**Parse the response body shape** (e.g. "does `all` exist? then it is v9").
Rejected as inference dressed up as detection: it couples the compatibility
check to an incidental payload difference that may vanish in a later version,
and it says nothing about versions we have never seen.

**Check compatibility once at startup and cache it.** Rejected as the sole
mechanism. The Paperless instance can be upgraded, or the URL repointed, while
PaperWrench is running. Mapping 406 centrally means every request re-validates
at no extra cost; `check_connection()` remains useful for reporting state to the
UI, not as a gate the rest of the code depends on.
