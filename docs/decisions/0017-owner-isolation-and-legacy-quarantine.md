# ADR-0017: local resources are owner-scoped; legacy rows are quarantined

The numeric user ID returned by the configured Paperless instance is the owner
identity for collections, schemas, previews and jobs. Job targets, operations,
rollback provenance and persisted document IDs inherit access exclusively through
their parent job or preview. Every list and ID lookup includes that owner; a guessed
ID from another user receives the same not-found/stale response as an unknown ID.

PaperWrench has no cross-user administrator view. A Paperless superuser still sees
only their own PaperWrench metadata. Introducing support access later requires a
separate audited capability and cannot be inferred from Paperless document access.

Workers bind an active session credential only after checking the durable job owner,
and check the same owner again before the first upstream call. Each target is freshly
read and authorized by Paperless at execution. If a token is revoked or permissions
change, Paperless's 401/403/404 response becomes failed, permission, or missing job
evidence; the worker never substitutes another user's credential.

The v0.1 schema had no trustworthy owner. Migration adds nullable owner columns but
does not guess. Existing collections, schemas, previews and jobs remain `NULL`-owned
and are invisible to every login. Operators may keep the database as audit evidence,
export it before upgrading, or recreate definitions explicitly under each account.
There is intentionally no "claim all legacy data" shortcut.
