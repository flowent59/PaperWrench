# Install, upgrade, back up and restore 0.1.0

This is a proposed first release, not a published image or tag. Build from the
reviewed M13 commit until a release is approved. The supplied Compose file uses
the local `paperwrench:0.1.0` image. Paperless 3.1.2 is the verified upstream;
other releases require separate validation. PaperWrench never reads its files
or database.

## Fresh installation

1. Check out the reviewed commit and copy `.env.example` to `.env`.
2. Set `PAPERLESS_URL` to an address reachable **from the container**, and
   `PAPERLESS_TOKEN` to a dedicated, minimally privileged Paperless user's token.
   `localhost` inside the container is not the Docker host. Keep TLS verification
   enabled for remote connections. Protect `.env` from other users.
3. Run `docker compose up -d --build`. This builds the SPA and wheel, creates
   the named `/data` volume and applies Alembic migrations automatically.
4. Check `docker compose ps`, `/api/v1/system/health` and the connection status
   at `http://127.0.0.1:8000`. Refresh a nested document/Job URL to check routing.
   A healthy local database does not imply Paperless connectivity.
5. Test a small preview and inspect its exact selection before confirming.

The image runs as UID/GID **10001**, one process and one worker. For a bind mount,
prepare a directory owned/writable by 10001; do not run the container as root to
work around volume permissions. Compose makes the root filesystem read-only,
drops capabilities and uses `/tmp` as tmpfs. Keep `/data` on reliable local disk;
SQLite WAL and the runtime lock are not a distributed deployment protocol.

For a file secret, mount it read-only (readable by UID 10001) and set
`PAPERLESS_TOKEN_FILE` to its **container** path; setting a host path alone does
not mount it. Remove the inline token afterward. The token must never be in a
Vite variable or browser configuration. The Docker build context excludes local
secrets, databases, caches and installed dependencies.

There is no built-in user authentication. Keep the loopback binding or put an
authenticated TLS reverse proxy in front of **all** routes, including `/api`,
assets and OpenAPI. See [security.md](security.md). Deploy at the origin root;
URL subpath hosting is not supported. Disable proxy retries for mutations and
allow at least the five-minute preview build window plus network overhead. A
lost Apply response means inspect History, never automatically submit again.

## Upgrade from M12 or earlier development checkouts

1. Record the old commit/image and database revision. Stop creating Jobs, wait
   for active Jobs to finish, and pause external writers during any document
   mutation. Resolve uncertain operations manually in Paperless.
2. Back up PaperWrench and Paperless separately as described below. Do not
   upgrade without a recoverable copy of the working history.
3. Stop the old application with `docker compose stop paperwrench`. Retain its
   volume; **do not use `down -v`**. Check out/build the reviewed new code and run
   `docker compose up -d --build` with the same project and volume.
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
