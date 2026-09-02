<div align="center">

# PaperWrench

**Power tools for Paperless-ngx**

Bulk transformations, metadata management, data quality and safe, reversible
mass edits for a library you already own.

[![CI](https://github.com/flowent59/PaperWrench/actions/workflows/ci.yml/badge.svg)](https://github.com/flowent59/PaperWrench/actions/workflows/ci.yml)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)

</div>

> **Not affiliated with Paperless-ngx.** PaperWrench is an independent,
> community project. It is not endorsed by, sponsored by, or associated with
> the Paperless-ngx project or its maintainers. "Paperless-ngx" is used here
> only to describe what this tool interoperates with.

---

## What PaperWrench is

PaperWrench is a self-hosted companion application for an existing
Paperless-ngx instance. It is the toolbox for everything that happens *after*
your documents are in Paperless: renaming three hundred documents from a
template, fixing a custom field across a whole document type, finding the
records that are missing a date, reviewing near-duplicates, and undoing all of
it when you get it wrong.

It talks to Paperless exclusively through the public REST API, the same one
your browser uses.

## What PaperWrench is not

This is the important part, and it is a design constraint rather than a
roadmap gap:

- **Not a document management system.** It does not store, index or serve your
  documents.
- **Not an OCR or consumption pipeline.** It never touches original files.
- **Not a storage backend.** It has no copy of your archive.
- **Not a replacement for Paperless-ngx.** Paperless-ngx remains the *sole
  source of truth*. If PaperWrench and Paperless disagree, Paperless is right.

PaperWrench's own database contains only its own working state: job history,
what it changed, and what the value was before it changed it. Delete it and
your library is untouched.

## Safety model

PaperWrench performs bulk writes on a live document library. The whole design
follows from taking that seriously.

- **Dry Run is the default.** Every transformation is previewed - old value
  next to new value, per document - and nothing is written until you
  explicitly confirm.
- **Nothing is written blind.** Before each write PaperWrench re-reads the
  document and checks that its current value still matches what you were shown.
  If somebody else changed it in the meantime, that document is skipped and
  reported, not overwritten.
- **Custom fields are never collateral damage.** Paperless replaces a
  document's *entire* custom field collection on update. PaperWrench always
  reads the existing fields and sends them back intact alongside the one it is
  changing.
- **Every write is recorded and reversible.** For each document PaperWrench
  stores what the value was, what it intended to write, and what it actually
  wrote. A rollback restores the previous value - and refuses, per document, if
  the value has changed since.
- **Bounded concurrency.** Writes are throttled (4 at a time by default) so a
  bulk job cannot overwhelm your Paperless instance.
- **The API token stays in the backend.** It is read from the environment,
  never persisted in the database, never logged, and never sent to the browser.

## Status

**Early development.** M0 (foundations) is complete: project skeleton, backend
and frontend build, database schema, single-instance job runtime lock, CI, and
a disposable Paperless-ngx 3.1.2 development sandbox.

Nothing writes to Paperless yet. See [docs/roadmap.md](docs/roadmap.md).

## Quick start

Requires an existing Paperless-ngx instance and an API token
(*Settings > My Profile > API Auth Token*).

```bash
git clone https://github.com/flowent59/PaperWrench.git
cd PaperWrench
cp .env.example .env
$EDITOR .env          # set PAPERLESS_URL and PAPERLESS_TOKEN
docker compose up -d
```

PaperWrench is then available at <http://localhost:8000>.

The compose file binds to `127.0.0.1` on purpose. See
[Threat model](#threat-model) before exposing it.

## Development

```bash
make install          # backend venv + frontend deps
make check            # lint, typecheck and tests, exactly as CI runs them
make dev-backend      # API on :8000
make dev-frontend     # Vite dev server on :5173
```

Never develop against your real library. A disposable, pre-seeded
Paperless-ngx 3.1.2 instance is one command away:

```bash
make dev-paperless-up     # Paperless-ngx 3.1.2 on :8010
make dev-paperless-seed   # reference dataset, French metadata included
```

Full instructions in [docs/development.md](docs/development.md).

## Threat model

Read this before deciding where to run PaperWrench.

**PaperWrench has no authentication of its own.** It is designed to run on a
trusted network - a home LAN, a private VLAN, behind a VPN, or behind a reverse
proxy that performs authentication. Anyone who can reach the PaperWrench port
can perform bulk modifications on your Paperless library using your token.

What this means concretely:

- **Do not expose PaperWrench to the internet** without an authenticating
  reverse proxy in front of it (Authelia, Authentik, oauth2-proxy, basic auth,
  a VPN - anything that terminates identity).
- **The token is as powerful as the user it belongs to.** Create a dedicated
  Paperless user with only the permissions you are willing to delegate, rather
  than using an administrator token.
- **Use `PAPERLESS_TOKEN_FILE`** with a Docker secret in preference to an
  inline environment variable, which is visible to anything that can inspect
  the container.
- **Back up `/data`.** It holds the job history, and the job history is what
  makes a rollback possible. Losing it after a large write means losing the
  ability to undo that write.
- **Keep TLS verification on.** `PAPERLESS_VERIFY_SSL=false` exists for
  self-signed certificates on a LAN, and it removes protection against an
  active network attacker.

What PaperWrench does *not* do: it never sends your token to the browser, never
writes it to its database, never includes it in logs (log output is scrubbed at
the logging layer, not at each call site), and never contacts any third-party
service. There is no telemetry.

## Documentation

| Document | Contents |
| --- | --- |
| [docs/architecture.md](docs/architecture.md) | System design and component boundaries |
| [docs/architecture-review.md](docs/architecture-review.md) | The full pre-implementation review |
| [docs/paperless-api.md](docs/paperless-api.md) | Verified Paperless-ngx API behaviour and hazards |
| [docs/development.md](docs/development.md) | Local setup and workflow |
| [docs/roadmap.md](docs/roadmap.md) | Milestones M0-M13 |
| [docs/decisions/](docs/decisions/) | Architecture Decision Records |

## Contributing

Issues and pull requests are welcome. Please run `make check` before opening a
pull request; CI runs the same gates.

Given what this tool does, changes to the write path are held to a higher
standard: a pull request that can modify documents must come with tests
covering conflict detection and custom-field preservation.

## License

[GPL-3.0-or-later](LICENSE). Copyright (C) 2026 PaperWrench contributors.

This program is free software: you can redistribute it and/or modify it under
the terms of the GNU General Public License as published by the Free Software
Foundation, either version 3 of the License, or (at your option) any later
version. It is distributed in the hope that it will be useful, but **WITHOUT
ANY WARRANTY**; without even the implied warranty of MERCHANTABILITY or FITNESS
FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.
