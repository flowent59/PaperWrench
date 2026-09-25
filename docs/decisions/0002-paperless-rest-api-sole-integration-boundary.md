# ADR-0002: The Paperless REST API is the sole integration boundary

## Status

Accepted (M0).

Compatibility qualification: [ADR-0008](0008-api-compatibility-is-decided-by-status-code.md)
supersedes the header-negotiation assumption below. HTTP 406 rejects the requested
version; X-Api-Version advertises the server maximum, not the negotiated version.

## Context

PaperWrench needs to read and modify documents that live in Paperless-ngx.
Several integration paths exist, and they differ enormously in risk.

Paperless stores its data in PostgreSQL, MariaDB or SQLite, and the schema is
right there. Direct database access would be faster, would allow set-based
updates, and would sidestep the API's quirks entirely. It is also the single
most dangerous thing this project could do. Paperless maintains a search index,
thumbnails, an archive of derived files, tag matching side effects, audit
logging and a task queue. A row updated behind Paperless's back leaves all of
that inconsistent, in ways the user discovers weeks later when a search stops
returning a document. Worse, the schema carries no compatibility guarantee: it
changes between minor releases, and a tool that reads it becomes a liability at
every upgrade.

The Paperless filesystem is equally off-limits: originals, archive versions and
thumbnails are managed by the consumption pipeline, and the storage layout is
driven by user-configurable filename templates.

The REST API, by contrast, is the interface Paperless's own frontend uses. It
is versioned, it enforces permissions, and it triggers the correct side effects
(index updates, audit entries, tag matching).

There is one significant caveat, discovered during the pre-implementation
review: the API is not a perfectly safe abstraction either. Its document
serialiser uses `drf_writable_nested.NestedUpdateMixin`, which makes a PATCH of
`custom_fields` a full replacement rather than a merge. That hazard is real,
but it is *knowable and testable*, which direct database access is not.

## Decision

PaperWrench interacts with Paperless-ngx exclusively through the public REST
API over HTTP.

- No direct database connection to Paperless, ever, under any configuration
  flag. There is no escape hatch to add later.
- No reads or writes to the Paperless filesystem, including thumbnails and
  archive files.
- No assumptions about Paperless internals beyond documented API behaviour, and
  every behaviour we do rely on is recorded in `docs/paperless-api.md` with its
  verification status.
- API version negotiation is explicit: every request sends
  `Accept: application/json; version=10`. Verified against Paperless-ngx 3.1.2,
  where `ALLOWED_VERSIONS = ["9", "10"]` and `DEFAULT_VERSION = "10"`. Pinning
  matters because the server default will move in a future release, and we want
  a loud 406 rather than a silent change in response shape.
- The response's `X-Api-Version` / `X-Version` headers are checked so an
  incompatible server is reported clearly at connection time instead of causing
  confusing failures later.
- Paperless-ngx is the sole source of truth. PaperWrench's database holds only
  its own working state - jobs, operations, previous values - and never a cached
  copy of documents that could drift.

Authentication uses a single API token supplied by the operator via
`PAPERLESS_TOKEN` or `PAPERLESS_TOKEN_FILE`. It is held in the backend process
only: never persisted to the database, never logged (redaction happens in a
structlog processor, so an ad-hoc log call cannot leak it), and never sent to
the browser. `/system/info` deliberately exposes booleans and versions only.

## Consequences

Upgrades of Paperless are far less likely to break PaperWrench, and when they
do the failure is an explicit API error rather than silent corruption. All
Paperless permission checks apply automatically: a token with limited rights
simply cannot do more than its user could through the UI.

We accept being slower. Bulk operations become N HTTP requests rather than one
`UPDATE` statement, which is exactly why the write path needs bounded
concurrency (ADR-0003). We also accept being limited to what the API exposes:
where the API cannot express a filter, we refuse rather than work around it
(ADR-0007).

Because the API surface is our entire contract with Paperless, its verified
behaviour is documentation we maintain, and integration tests run against a
pinned, disposable Paperless-ngx 3.1.2 instance rather than any real library.

## Alternatives considered

**Direct database access for reads, API for writes.** Tempting for analytics
performance. Rejected: it still couples us to an unversioned schema, and a
read-side that disagrees with the write-side is a preview that lies.

**Paperless post-consumption scripts / plugins.** Rejected: they run inside the
consumption pipeline, which is the one part of Paperless PaperWrench is
explicitly not involved in.

**Requiring a specific Paperless fork or patch.** Rejected outright: users must
keep the Paperless they already run.
