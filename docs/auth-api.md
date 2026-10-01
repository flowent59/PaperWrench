# Authentication and remembered credentials

All routes below live under `/api/v1/auth`. Requests and responses use JSON;
responses (including errors) are `Cache-Control: no-store`. Secrets never appear
in responses. State-changing browser requests must pass the existing origin guard.

| Method / path | Request | Result |
| --- | --- | --- |
| `GET /options` | None; public | `{ "remember_available": true }` when globally enabled and a master key is configured. |
| `POST /login` | `{ "token": "…", "locale": "en", "remember": true, "password": "…" }` | Validates the Paperless token, obtains identity, saves credentials when requested and creates a session. |
| `POST /password-login` | `{ "username": "alice", "password": "…" }` | Verifies the local password, decrypts and revalidates the saved token, then creates a session. |
| `GET /me` | Session cookie | Current session including `remembered`. |
| `PUT /credentials` | Session + CSRF; `{ "token": "…", "password": "…" }` | Validates a token for the same identity, replaces credentials, rotates the current session and invalidates other owner sessions. |
| `DELETE /credentials` | Session + CSRF | Removes saved credentials, invalidates all owner sessions and clears the cookie; `204`. Application data remains. |
| `POST /logout` | Session + CSRF | Destroys the current session; `204`. Saved credentials remain. |

Session responses contain `user_id`, `username`, `display_name`, `expires_at`,
`csrf_token`, `locale` and `remembered`. Cookies are opaque, HttpOnly and
SameSite=Strict, with Secure determined by the deployment setting/scheme.

For backward compatibility, omitted `remember` means false; the UI chooses true
by default when available. Opting out does not remove previously saved credentials.
The username is never accepted by enrollment; it comes from Paperless. New local
passwords require 12–1024 characters; tokens are bounded to 4096 characters.
`locale` remains optional (`en` by default) and preserves the existing preference.

Identity comes from the validated profile, or its authenticated UI-settings
fallback on Paperless versions whose profile omits it. The fallback requires
UI-settings view permission. Local owner keys remain stable through a separate
non-secret identity binding, retained even after saved credentials are deleted.

A valid Paperless token is sufficient recovery proof, so enrollment for an
existing identity replaces its saved token/password without asking for the old
local password. Username conflicts with another identity fail without adoption.
Password login also refuses changed usernames, instance URLs, tampered ciphertext,
missing keys, revoked tokens and identity mismatches. Supply a fresh valid token
to recover; if the upstream username changed, enrollment refreshes it.

`AUTH_INVALID_CREDENTIALS` is the common `401` for unknown username, wrong
password, unrecoverable token or invalid upstream credential. Storage unavailable
returns `503 AUTH_STORAGE_UNAVAILABLE`. Unsupported identity or a conflicting
identity returns `409 AUTH_IDENTITY_UNSUPPORTED` / `AUTH_IDENTITY_CONFLICT`.
Ten login/replacement attempts per minute per direct peer are allowed; additional
attempts return `429 AUTH_RATE_LIMITED`. This is an in-process limit, reset on
restart, and proxied clients can share a bucket. It does not trust forwarded IPs.

Master key configuration and backup/recovery limits are documented in
[deployment.md](deployment.md#remembered-credentials).
