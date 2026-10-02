# ADR-0019: optional local accounts remember encrypted Paperless credentials

## Status

Accepted for issue #64; qualifies ADR-0016's prohibition on persisted credentials.

## Context

NAS users should reconnect after a restart without retrieving their Paperless
token each time. Sessions and background-job authorization must remain temporary.

## Decision

Any holder of a valid Paperless token may enroll without administrator approval.
The server binds the account to the upstream identity and username; clients may
not choose an enrollment username. One identity owns one local account and its
existing owner-scoped data. Accounts cannot be rebound by username.

Enrollment requires a verified stable numeric ID and username. `/api/profile/`
always validates the token first. Because the 3.2.1 profile omits the identity,
`/api/ui_settings/` supplies its authenticated `user` as a compatibility fallback.
The endpoint requires UI-settings view permission, not administrator/user-list
access. Without that permission and without identity in the profile, token-only
login remains available and memorization fails explicitly. This qualifies the
issue's assumption that `/api/profile/` always supplies identity for any token.
Email and display name never prove identity.

`paperless_identities` binds the verified upstream ID and instance URL to the
existing local owner key. First login preserves the legacy fingerprint owner;
subsequent credentials for the same verified identity reuse it, including after
token rotation. This non-secret binding survives deletion of saved credentials,
so forgetting a token does not abandon application data. No resource rows are
copied, reassigned or merged between identities. Credentials and identity
bindings remain separate so token-only users also keep stable ownership.

The UI offers memorization, selected by default when configured, and always
permits token-only login. Legacy API callers omit `remember` and retain their
existing behavior. An operator can disable memorization globally. A missing
master key makes it unavailable without breaking existing deployments.

`local_credentials` stores an Argon2id hash and Fernet ciphertext. The encrypted
payload includes the owner ID, username and instance URL to detect row swapping.
The operator provides the master key separately; no generated key is stored in
SQLite. Passwords are 12–1024 characters and are never recoverable. Password
verification is bounded and off the event loop; unknown accounts use dummy work.

Password login decrypts only in memory and validates the token with Paperless
before issuing the usual opaque session cookie. Revalidation, absolute expiry,
HttpOnly, SameSite, Secure configuration, CSRF and origin protection remain.
Concurrent deletion or replacement during validation prevents session creation.

A valid upstream token proves recovery authority and may replace the saved token
and password for the same identity. Replacement revokes other sessions; deletion
revokes all of that owner's sessions but preserves application data. New tokens
for other identities never adopt old data. No Paperless account is created or
modified. Sessions and job execution authority are never restored automatically.

## Consequences

Back up the key separately from the database. Loss of the key requires enrollment
again; changing it is not an automatic ciphertext migration. Host/container and
configuration compromise can expose all saved tokens. SQLite deletion is logical,
so old pages and backups can retain ciphertext. Global disable retains existing
rows, allows deletion, and prevents their use for sign-in or replacement.

No email recovery, MFA, SSO, local administrators or cross-identity data adoption
is introduced. Token-only login remains available for legacy deployments.

## References

- [Fernet authenticated encryption](https://cryptography.io/en/stable/fernet/)
- [Argon2 password hashing API](https://argon2-cffi.readthedocs.io/en/stable/api.html)
- [Paperless 3.2.1 authenticated UI settings view](https://github.com/paperless-ngx/paperless-ngx/blob/v3.2.1/src/documents/views.py)
