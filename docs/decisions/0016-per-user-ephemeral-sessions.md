# ADR-0016: per-user credentials live in ephemeral server sessions

PaperWrench validates each submitted API token against Paperless `/api/profile/`
and uses the returned numeric user ID as the upstream identity. The browser gets
an opaque `HttpOnly`, `SameSite=Strict` cookie plus a non-credential CSRF token.

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
