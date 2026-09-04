# ADR-0009: Metadata Registry — a TTL cache, never a second source of truth

## Status

Accepted (M2).

## Context

M2 needed a way to resolve a tag, correspondent, document type, storage path
or custom field by either its Paperless id or its name, for use by every
future milestone that lets a user refer to metadata by name — filters (M4),
the inspector (M5), transformations (M6). Two questions had to be answered
before writing any code:

1. What happens when a name does not uniquely identify one object?
2. How much can this be cached without becoming a second, driftable copy of
   Paperless's own state — the exact anti-pattern ADR-0001/0002 already rule
   out for documents?

On (1): Paperless enforces a per-owner uniqueness constraint on tag and
custom field names (`VERIFIED_LIVE`), so a same-owner collision cannot happen
through Paperless's own API today. But nothing stops two objects owned by
*different* users from sharing a name, and PaperWrench must not assume
single-owner operation forever just because it is convenient right now. The
Golden Dataset also deliberately contains near-duplicate names that must
**not** be treated as the same object (`Etablissement` / `Établissement`,
`Periode concernee` / `Période concernée`, `Reference interne` /
`Référence interne`, `Valide` / `Validé`) — proof that "close enough" name
matching is actively dangerous here, not just unnecessary.

On (2): every metadata read PaperWrench will ever do to resolve a name is
already an extra round trip on top of the one the actual operation needs.
Refetching the entire tag/correspondent/document-type/storage-path/
custom-field catalogue on every single lookup does not scale once a job
touches hundreds of documents, each potentially needing a name resolved. But
ADR-0001/0002's rule — Paperless is the sole source of truth, nothing else
may drift from it — applies just as much to a tag's name as to a document's
content.

## Decision

`paperless/registry.py` implements `MetadataRegistry` as a small,
in-process, per-kind **TTL cache**, not a database:

- One cache entry per metadata kind (tag, correspondent, document type,
  storage path, custom field), each independently fetchable, refreshable and
  invalidatable — a stale tag list must never block a fresh custom-field
  lookup.
- `is_expired()` is computed from `time.monotonic()`, never wall-clock time:
  wall-clock can jump (NTP correction, DST, a container pause) in ways that
  would make "expired" lie in either direction.
- No SQLite table backs this. Restarting the process empties it; there is
  nothing to migrate, back up, or get out of sync on disk.
- `refresh(kind)` and `invalidate(kind)` are both explicit operations a
  caller can request after a write it knows changed reference data (e.g.
  creating a tag), rather than only ever waiting out the TTL.
- **Ambiguity is never silently resolved.** `*_by_name()` does a fresh linear
  scan of the current snapshot on every call (not a separately-maintained
  name index, which could itself go stale independently of the id index) and
  raises `AmbiguousMetadataName` — carrying every matching id — the moment
  more than one object shares the requested name. There is no "return the
  first one" fallback anywhere in this module.
- **Name resolution is exact-match only.** No accent-folding, no
  case-insensitivity, no trimming beyond whatever Paperless itself already
  applied on write. A resolver that normalises names would conflate the
  deliberately-distinct Golden Dataset pairs listed above.

## Consequences

A lookup can be up to `ttl_seconds` (default 60s) stale relative to
Paperless. This is an accepted, bounded staleness window — not a claim that
the registry is authoritative. Every consumer of this registry must treat its
answers as "true as of at most `ttl_seconds` ago", and any code path where
that staleness is unacceptable (e.g. immediately after the caller itself
created the object being looked up) must call `refresh()`/`invalidate()`
explicitly rather than assume the cache saw the write.

`AmbiguousMetadataName` and `MetadataNotFoundError` are real error states a
caller — eventually the Filter Engine (M4) and the Inspector (M5) — must
handle, not edge cases that can be waved away. A UI that lets a user type a
tag name must be able to render "which one did you mean?" using
`matching_ids`.

Because the registry holds no SQLite state, there is nothing to reconcile
across PaperWrench restarts or across a future multi-worker deployment: each
process simply refetches on first use. This trades a small amount of
duplicated network traffic for the complete absence of a metadata
consistency problem to solve later.

## Alternatives considered

**Mirror metadata into SQLite, refreshed by a background sync.** Rejected:
this is precisely the "second source of truth" ADR-0001/0002 forbid for
document data, and there is no reason metadata should be exempt. A sync job
is also itself a source of staleness bugs (what if the sync fails silently?),
which an on-demand TTL cache does not introduce.

**Normalise names for lookup (case/accent-insensitive).** Rejected: the
Golden Dataset was specifically seeded with near-duplicate names to prove
this would be wrong. A resolver that "helpfully" matched
`Etablissement`/`Établissement` would silently merge two custom fields a
user (or an earlier migration) deliberately kept separate.

**Trust Paperless's per-owner uniqueness constraint and skip ambiguity
detection entirely.** Rejected: it is true only for same-owner objects today,
and baking that assumption into the registry would make a future
multi-owner scenario fail by returning a silently wrong object rather than a
clear error.
