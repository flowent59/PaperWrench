# Install, upgrade, back up and restore v0.2.0

This guide covers fresh multi-user installations and upgrades from PaperWrench
v0.1.x. Use the Compose files from the release you are installing: their default
`PAPERWRENCH_IMAGE` is updated with each published version. CI exercises the
current stable Paperless-ngx `latest` image and records its digest and reported
version for each run. PaperWrench uses only the Paperless REST API; it never reads the
Paperless database or document files.

PaperWrench has no local user database and no bootstrap administrator. Every
person signs in with their own Paperless API token. A fresh v0.2 deployment needs
`PAPERLESS_URL`, but must not configure `PAPERLESS_TOKEN` or
`PAPERLESS_TOKEN_FILE`.

## LAN installation alongside an existing Paperless stack

1. Get the reviewed Compose file and a matching release checkout. Find the Docker network shared by
   your Paperless service (`docker compose -f /path/to/paperless/compose.yml ps`
   and `docker network ls`); it is often `<paperless-project>_default`. Note the
   Paperless **service name**, often `paperless` or `webserver`, not its container
   name or public URL. The service must listen on port 8000 inside that network.
2. Ensure every intended user can create an API token in Paperless and has only
   the global/object permissions they need. No shared token is configured in
   PaperWrench.
3. Start the supplied Compose example, substituting your network, service name
   and preferred host port:

   ```sh
   export PAPERLESS_DOCKER_NETWORK=paperless_default PAPERLESS_SERVICE=paperless PAPERWRENCH_HTTP_PORT=8000
   docker compose -f docker-compose.paperless.yml pull
   docker compose -f docker-compose.paperless.yml up -d
   ```

4. Check `docker compose -f docker-compose.paperless.yml ps`, open
   `http://IP_DU_SERVEUR:PORT`, sign in with your own Paperless token, then
   verify the connection status in the UI or at `/api/v1/system/paperless`.
   The SPA and API use the same HTTP origin, so no CORS allowlist is needed. A
   healthy `/api/v1/system/health` only proves the local database is available.
   Test a small preview before confirming a write.

The example publishes only PaperWrench's HTTP port. Paperless is reached at
`http://<PAPERLESS_SERVICE>:8000` through its existing Docker network, even if
its host port or public URL differs. `PAPERLESS_DOCKER_NETWORK` names an
**external network** that must already exist; Compose does not create or remove
it. If Paperless uses a different internal port, edit `PAPERLESS_URL` in the
example. Set `PAPERWRENCH_HTTP_PORT` to avoid a host-port collision. Keep this
port on a trusted LAN or restrict it with the host firewall. Login prevents use
of a shared credential but does not encrypt an HTTP-only connection.
If GHCR reports that the package is private or missing, wait for publication
and public package visibility before using this pull-based installation.

To build the reviewed checkout locally before publication or for development,
use the optional override with the same network and secret settings:

```sh
PAPERLESS_DOCKER_NETWORK=paperless_default docker compose \
  -f docker-compose.paperless.yml -f docker-compose.build.yml up -d --build
```

This produces `paperwrench:local` and leaves the standard Compose file
pointing at the versioned GHCR image. The image is built from this checkout.
The same override works with `docker-compose.yml` for loopback/proxy deployments.

The image runs as UID/GID **10001**, one process and one worker. For a bind mount,
prepare a directory owned/writable by 10001; do not run the container as root to
work around volume permissions. Compose makes the root filesystem read-only,
drops capabilities and uses `/tmp` as tmpfs. Keep `/data` on reliable local disk;
SQLite WAL and the runtime lock are not a distributed deployment protocol.

The token must never be in a Vite variable or browser configuration. It is sent
only during login or explicit credential replacement. Optional memorization
stores it encrypted; otherwise it stays in server memory. The Docker
build context excludes local secrets, databases, caches and dependencies.

## First sign-in and Paperless permissions

Repeat these steps for every user; there is no shared or first-user credential:

1. Sign in to Paperless as the account that will use PaperWrench.
2. Open **My Profile** from the Paperless user menu. Under **API Auth Token**,
   use the circular-arrow action to create or regenerate the token. This is the
   token-authentication flow documented in the
   [Paperless REST API guide](https://docs.paperless-ngx.com/api/#authorization).
3. Copy the token directly into the PaperWrench sign-in form and submit it. Do
   not put it in `.env`, Compose, a URL, browser storage or a password manager
   shared with other PaperWrench users.
4. Confirm that the displayed Paperless identity is the intended account, then
   start with a read-only view or a small preview before applying a mutation.

Regenerating a Paperless token invalidates the previous token. PaperWrench API
tokens are tied to Paperless accounts; they are not independently scoped keys.
PaperWrench therefore inherits that account's current global and object-level
permissions. A user can see or edit only what Paperless returns for that user,
and Paperless rechecks permissions during every operation. See Paperless's
[permission model](https://docs.paperless-ngx.com/usage/#permissions). Use a
dedicated, least-privileged Paperless account instead of an administrator
account. Never give two people the same account or token: PaperWrench will
correctly treat them as the same Paperless identity, so they will share
PaperWrench-owned resources.

The token is exchanged for an opaque `HttpOnly`, `SameSite=Strict` cookie. It is
kept in PaperWrench process memory for the session. Logout, process restart,
absolute expiry or a revoked token detected during Paperless revalidation
destroys the session and the user signs in again. The relevant settings are:

| Setting | Default | Purpose |
| --- | ---: | --- |
| `PAPERWRENCH_SESSION_TTL_SECONDS` | `28800` | Absolute session lifetime (8 hours; allowed range 5 minutes to 7 days). |
| `PAPERWRENCH_SESSION_REVALIDATE_SECONDS` | `300` | Interval before PaperWrench asks Paperless to validate the token again; `0` checks every request. |
| `PAPERWRENCH_SESSION_COOKIE_SECURE` | `false` in Compose | Set `true` behind HTTPS when the original scheme is forwarded correctly. Direct application execution auto-detects it when unset. |

## Remembered credentials

With a master key configured, **Remember my Paperless token** is checked by
default. The user supplies their own valid Paperless token and chooses a local
PaperWrench password (12–1024 characters). PaperWrench obtains the username from
Paperless; enrollment never accepts a user-chosen username or needs administrator
approval. Subsequent sign-in uses **Local account**, that username and the local
password. This password is independent of the Paperless password.

Compatibility: PaperWrench first validates the token with `/api/profile/`.
When that response lacks a stable numeric `id` and `username` (including
Paperless 3.2.1), it reads the current user from `/api/ui_settings/`. This requires
Paperless's **UI settings: view** permission, without administrator privileges
or permission to list other users. If neither endpoint supplies the identity,
memorization fails explicitly; uncheck the option to use token-only login.
Email and display name are never used as local account identifiers.

On first verified sign-in, a non-secret identity binding preserves the existing
local owner key, including the legacy token fingerprint. Future tokens for that
same upstream identity reuse the binding; forgetting the saved token retains it.
Existing resources are neither reassigned to another identity nor merged.
See the [API contract](auth-api.md).

Set `PAPERWRENCH_REMEMBER_TOKENS=false` to disable enrollment, replacement and
password login globally. Existing encrypted rows remain available for deletion.
Without a master key, memorization is unavailable and token login still works.
Unchecking the option uses an ephemeral session and saves no new credentials;
it does not delete credentials saved previously.

Generate a key once in an environment with the backend dependencies installed:

```sh
python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

Supply it as `PAPERWRENCH_CREDENTIAL_KEY`, or preferably mount a Docker secret
and set `PAPERWRENCH_CREDENTIAL_KEY_FILE`. The file takes precedence; unreadable,
empty or malformed explicit key files prevent startup. For example, save the
generated key to a restricted file outside the data volume and add this Compose
override (adjust the source path):

```yaml
services:
  paperwrench:
    environment:
      PAPERWRENCH_CREDENTIAL_KEY_FILE: /run/secrets/paperwrench_credential_key
    secrets:
      - paperwrench_credential_key
secrets:
  paperwrench_credential_key:
    file: /secure/paperwrench-credential-key
```

Back up the master key **separately** from SQLite. It must be readable by the
container's UID 10001, but should not be exposed to other users. Losing it makes
saved tokens irrecoverable; users must enroll again with valid Paperless tokens.
Replacing the key does not automatically re-encrypt existing rows. A compromise
of the NAS/container together with its configuration can expose every saved
token. Encryption at rest does not protect a running compromised server.

**My account** shows the Paperless username and supports replacing the token and
local password for that same identity, or **Forget token and sign out**. Replacing
credentials invalidates the owner's other sessions. Forgetting them invalidates
all their sessions and removes both saved token and password hash, while retaining
their PaperWrench data. SQLite pages and old backups can retain deleted ciphertext;
deletion is not secure erasure.

A revoked token prevents password login. Use **Use a token** with a new valid
Paperless token and a local password to recover access; the valid upstream token
is the recovery proof, so the old local password is not required. A changed
Paperless username likewise requires token enrollment to refresh the local name.
Accounts remain bound to one stable Paperless identity and instance URL. They
cannot be transferred or merged by reusing a username. If changing the instance
URL, use a separate PaperWrench database or perform an explicit offline migration
of identity bindings; changing the URL never silently adopts another instance.

Sessions are never persisted: restart always requires sign-in. Saved tokens never
resume jobs automatically. Every password sign-in validates the recovered token
against Paperless, and normal session expiry, CSRF and periodic revalidation still
apply. Login attempts are limited to ten per minute per direct client address;
behind a proxy, clients may share this limit.

## Advanced: reverse proxy and HTTPS (optional)

For remote access, put an authenticated TLS reverse proxy in front of **all**
routes, including `/api`, assets and OpenAPI. Restrict direct access to the
PaperWrench port. Forward the original Host and scheme; trust forwarded headers
only from the proxy's addresses. Set `PAPERWRENCH_SESSION_COOKIE_SECURE=true`
and verify login through the public HTTPS origin before enabling writes. Leave
`PAPERWRENCH_CORS_ORIGINS` empty. See
[security.md](security.md). Deploy at the origin root; URL subpath hosting is
not supported. Disable proxy retries for mutations and allow at least the
five-minute preview build window plus network overhead. A lost Apply response
means inspect History, never automatically submit again. The original
`docker-compose.yml` binds to loopback for this style of deployment.

## Upgrade from v0.1.0 or v0.1.1

Legacy `PAPERLESS_TOKEN` and `PAPERLESS_TOKEN_FILE` values are not imported into
user sessions and grant nobody access. Remove them before upgrading. This is
deliberate: automatically assigning the former shared token would silently give
every visitor its privileges. After the upgrade each operator signs in with an
individual Paperless token; process restart and logout require signing in again.

The ownership migration likewise leaves all v0.1 collections, schemas, previews,
jobs and history unowned and invisible. Back up/export anything needed before the
upgrade, then recreate definitions under the appropriate account. Do not edit
`owner_id` manually: a guessed assignment can expose document IDs, before/written
values and rollback authority to the wrong person. PaperWrench has no cross-user
administrator view by default.

1. Record the old version/image and database revision. Stop creating Jobs, wait
   for active Jobs to finish, and pause external writers during any document
   mutation. Resolve uncertain operations manually in Paperless.
2. Back up PaperWrench and Paperless separately as described below. Do not
   upgrade without a recoverable copy of the working history.
3. Remove `PAPERLESS_TOKEN` and `PAPERLESS_TOKEN_FILE` from `.env`, Compose,
   Docker secrets and the service environment. Keep `PAPERLESS_URL`; verify it
   is reachable from the PaperWrench container.
4. Stop the old application with `docker compose stop paperwrench`. Retain its
   volume; **do not use `down -v`**. Pull the reviewed v0.2 image and run
   `docker compose up -d` with the same project and volume. Use the local-build
   override above only when building a reviewed checkout yourself.
5. Check `/api/v1/system/health`, sign in separately as each intended user, and
   verify `/api/v1/system/paperless`. Confirm that old schemas, collections,
   previews and History are not visible to any account. Recreate definitions
   under their correct owners; do not copy or assign database owner IDs.
6. Create a small preview under each representative permission set before any
   write. Review interrupted Jobs; resume only an owned, unsent target explicitly.
   Startup sends no automatic document writes. Ambiguous targets are never replayed.

The multi-user upgrade advances Alembic head to `d2f3a401b812`. Fresh install,
repeated upgrade and populated v0.1 backup restoration are tested. The existing
pre-M8 migration preserves legacy evidence as ambiguous/manual-review targets;
the ownership migration preserves it as unowned, invisible evidence. Old previews
require a new authenticated preview. `PAPERWRENCH_PAPERLESS_API_VERSION` remains
10 by default.

These behaviors prevent unintended privilege escalation: the former deployment
token creates no session, legacy rows acquire no owner, and no logged-in user or
PaperWrench administrator can claim or inspect another user's local history.

## Troubleshooting sign-in and sessions

| Symptom | Meaning and action |
| --- | --- |
| Sign-in reports an invalid or unauthorized token | Confirm the token was copied from the intended Paperless account. Regenerate it in **My Profile**, then sign in again. Regeneration revokes the old token and all PaperWrench sessions using it fail at their next revalidation. |
| A session returns to the sign-in page | The absolute lifetime elapsed, PaperWrench restarted, the user logged out, or Paperless rejected revalidation. Sessions are memory-only; sign in with the local account if enrolled, or supply a valid token again. |
| A document or metadata object is missing | Paperless can hide unauthorized objects as `404 Not Found`. Check the account's global and object-level view permissions in Paperless; do not use an administrator token to bypass diagnosis. |
| Preview or apply reports a permission failure | The account lacks the required current edit permission, or it changed after preview. Correct the permission in Paperless and build a new preview; PaperWrench never falls back to a deployment token. |
| Paperless is unavailable | From inside the PaperWrench container, verify DNS, port, network attachment and `PAPERLESS_URL`. `/api/v1/system/health` can remain healthy because it checks PaperWrench's local database, not Paperless. |
| HTTPS login loops or the cookie is absent | Ensure the proxy forwards the original Host and HTTPS scheme, the browser uses the public HTTPS origin, and `PAPERWRENCH_SESSION_COOKIE_SECURE=true`. Do not expose the backend port as an alternate HTTP origin. |
| Existing v0.1 resources disappeared | This is the intentional ownership quarantine, not deletion. Restore the pre-upgrade backup with the matching v0.1 image for audit/export, or recreate definitions under the correct v0.2 user. Never assign `owner_id` manually. |

## Backup

PaperWrench history, before/written values, schemas and collections reside in
`/data/paperwrench.db`. This contains sensitive metadata, even though it has no
document files. Encrypt and restrict backups; include non-secret configuration
separately and record the image/commit plus Alembic head. Remembered tokens are
encrypted in SQLite, alongside local password hashes. Store the master-key backup
separately, with restricted access; database backups must never contain that key.

Quiesce Jobs first. The standard-library SQLite backup API copies committed WAL
pages consistently while the process is running:

```sh
docker compose exec -T paperwrench python -c 'import sqlite3; s=sqlite3.connect("/data/paperwrench.db"); d=sqlite3.connect("/data/paperwrench-backup.db"); s.backup(d); d.close(); s.close()'
docker compose cp paperwrench:/data/paperwrench-backup.db ./paperwrench-backup.db
```

Alternatively stop the application and copy the entire volume, including any
`-wal` and `-shm` sidecars. Never copy only the main database file while live.
Keep enough free disk for the snapshot. Copy it off the application volume and
verify `PRAGMA integrity_check` returns `ok` and `PRAGMA foreign_key_check` has
no rows. Periodically rehearse restoration into a separate isolated deployment.

Back up Paperless with its own supported procedures, including its database,
originals and archive. A PaperWrench backup alone cannot restore documents.

## Restoration and downgrade

Stop every PaperWrench process using the destination volume. Preserve the current
volume for investigation; restore into a **new empty volume** with UID/GID 10001
ownership, using the matching code/image and configuration. A fresh destination
avoids mixing an old snapshot with newer WAL sidecars. Start one instance only,
initially isolated from Paperless, check database integrity and inspect History.

Before reconnecting, verify that the snapshot and Paperless refer to the same
library and point in time. Restoring old history does not undo upstream writes;
it may reintroduce unsent-looking work. Do not resume or roll back blindly.
Automatic startup recovery sends nothing, but cannot discover work absent from
an old backup. Reconcile with Paperless manually before an explicit action.

If a runtime lock from the backup is still fresh, wait at least 60 seconds after
the old process stops. Do not force lock takeover while another process could
send writes. To revert an upgrade, use the old image with its matching pre-upgrade
backup after reconciling any intervening writes. In-place Alembic downgrade is
not the supported operator rollback procedure.
