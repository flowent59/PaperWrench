# Install, upgrade, back up and restore 0.1.0

This guide covers the first release. After the generated release PR is merged
and its image validation succeeds, the default Compose file pulls
`ghcr.io/flowent59/paperwrench:0.1.0` without local compilation. Paperless-ngx
3.2.1 is the fixed verified upstream. In the recorded compatibility run,
Paperless `latest` resolved to that same 3.2.1 image digest; it did not exercise
a newer release. PaperWrench never reads Paperless files or database.

## LAN installation alongside an existing Paperless stack

1. Get the reviewed Compose file and a matching release checkout. Find the Docker network shared by
   your Paperless service (`docker compose -f /path/to/paperless/compose.yml ps`
   and `docker network ls`); it is often `<paperless-project>_default`. Note the
   Paperless **service name**, often `paperless` or `webserver`, not its container
   name or public URL. The service must listen on port 8000 inside that network.
2. In the PaperWrench checkout, create `secrets/paperless_token` containing only
   a dedicated, minimally privileged Paperless user's API token. Keep this file
   private. The `secrets/` directory is Git-ignored. Compose mounts it read-only
   at `/run/secrets/paperless_token`; the token is absent from the container
   environment and browser responses.
3. Start the supplied Compose example, substituting your network, service name
   and preferred host port:

   ```sh
   mkdir -p secrets
   chmod 700 secrets
   ${EDITOR:-vi} secrets/paperless_token
   chmod 600 secrets/paperless_token
   export PAPERLESS_DOCKER_NETWORK=paperless_default PAPERLESS_SERVICE=paperless PAPERWRENCH_HTTP_PORT=8000
   docker compose -f docker-compose.paperless.yml pull
   docker compose -f docker-compose.paperless.yml up -d
   ```

4. Check `docker compose -f docker-compose.paperless.yml ps`, open
   `http://IP_DU_SERVEUR:PORT`, and verify the connection status in the UI or at
   `/api/v1/system/paperless`. The SPA and API use the same HTTP origin, so no
   CORS allowlist is needed. A healthy `/api/v1/system/health` only proves the
   local database is available. Test a small preview before confirming a write.

The example publishes only PaperWrench's HTTP port. Paperless is reached at
`http://<PAPERLESS_SERVICE>:8000` through its existing Docker network, even if
its host port or public URL differs. `PAPERLESS_DOCKER_NETWORK` names an
**external network** that must already exist; Compose does not create or remove
it. If Paperless uses a different internal port, edit `PAPERLESS_URL` in the
example. Set `PAPERWRENCH_HTTP_PORT` to avoid a host-port collision. Keep this
port on a trusted LAN or restrict it with the host firewall: PaperWrench has no
built-in login and anyone who can reach it can exercise the configured token.
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

The token must never be in a Vite variable or browser configuration. The Docker
build context excludes local secrets, databases, caches and dependencies.

## Advanced: reverse proxy and HTTPS (optional)

For remote access, put an authenticated TLS reverse proxy in front of **all**
routes, including `/api`, assets and OpenAPI. Restrict direct access to the
PaperWrench port. Forward the original Host and scheme; trust forwarded headers
only from the proxy's addresses. Leave `PAPERWRENCH_CORS_ORIGINS` empty. See
[security.md](security.md). Deploy at the origin root; URL subpath hosting is
not supported. Disable proxy retries for mutations and allow at least the
five-minute preview build window plus network overhead. A lost Apply response
means inspect History, never automatically submit again. The original
`docker-compose.yml` binds to loopback for this style of deployment.

## Upgrade from M12 or earlier development checkouts

1. Record the old commit/image and database revision. Stop creating Jobs, wait
   for active Jobs to finish, and pause external writers during any document
   mutation. Resolve uncertain operations manually in Paperless.
2. Back up PaperWrench and Paperless separately as described below. Do not
   upgrade without a recoverable copy of the working history.
3. Stop the old application with `docker compose stop paperwrench`. Retain its
   volume; **do not use `down -v`**. Pull the reviewed versioned image and run
   `docker compose up -d` with the same project and volume. Use the local-build
   override above only when building a reviewed checkout yourself.
4. Check health, connection, schemas, collections, History and nested routes.
   Review interrupted Jobs; resume only unsent targets explicitly. Startup sends
   no automatic document writes. Ambiguous targets are never replayed.

M12 and 0.1.0 share Alembic head `c814b207f001`; no new schema revision is needed.
Fresh install, repeated upgrade and M12 backup restoration are tested. The
existing pre-M8 migration preserves legacy evidence as ambiguous/manual-review
targets; it cannot create verified provenance. Old previews may require a new
preview. M13 corrects the Compose API-version variable spelling to
`PAPERWRENCH_PAPERLESS_API_VERSION` (default remains 10).

## Backup

PaperWrench history, before/written values, schemas and collections reside in
`/data/paperwrench.db`. This contains sensitive metadata, even though it has no
document files. Encrypt and restrict backups; include configuration separately,
store credentials in a secret manager, and record image/commit plus Alembic head.

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
