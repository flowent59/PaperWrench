# ADR-0016: per-user credentials live in ephemeral server sessions

Credential persistence is now qualified by [ADR-0019](0019-remembered-credentials.md):
optional local accounts store encrypted tokens with a separately supplied key.
The session lifetime, revalidation and explicit job-resume decisions still apply.
ADR-0019 also adds a verified upstream identity binding through UI settings when
the profile omits identity. Once bound, token rotation preserves the existing
local owner key instead of creating a new owner as described historically below.

PaperWrench validates each submitted API token against Paperless `/api/profile/`.
When that response contains a numeric user ID, it is the upstream identity.
Paperless 3.2.1 omits both ID and username and includes the token again instead;
for that contract PaperWrench derives a signed-64-bit SHA-256 fingerprint from
the already validated token and immediately discards the raw profile. This is a
stable, non-recoverable local owner key, not a credential. Rotating a Paperless
token intentionally creates a new local identity: old PaperWrench resources are
not silently adopted by a different credential. The browser gets an opaque
`HttpOnly`, `SameSite=Strict` cookie plus a non-credential CSRF token.

Tokens and authenticated HTTP clients live only in process memory. They are not
encrypted into SQLite because a colocated decryption key would add recoverable
credentials without a meaningful isolation boundary. Logout, absolute expiry,
upstream revocation, and restart destroy access. The trade-off is that interrupted
durable work needs the owner to authenticate and resume it after a restart.

Cookies automatically use `Secure` when the request scheme is HTTPS and can be
forced with `PAPERWRENCH_SESSION_COOKIE_SECURE`. HTTP is acceptable only on a
trusted LAN: it provides no confidentiality for the login request or cookie.

The v0.1 shared-token variables remain parseable for downgrade compatibility but
are never turned into a session. An upgrade therefore grants no user the former
shared identity implicitly.
