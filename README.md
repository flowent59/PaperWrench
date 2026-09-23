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

M5 exposes immediate, explicit single-document edits in the Inspector.

- **Read before write.** The document is re-read under a per-document in-process
  lock. A stale revision produces a non-retryable conflict without a PATCH.
- **Complete custom-field merge.** The client rejects arbitrary payload keys and
  partial replacement arrays. It preserves unrelated values from the fresh read.
- **External concurrency is not atomic.** Paperless 3.1.2 ignores the tested
  conditional headers. Another REST client, the Paperless UI or a consumer can
  change the document between our read and write, and that change can be lost.
  Pause other writers. Custom-field saves require explicit acknowledgement of
  this residual risk; it is not a guarantee that other writers are paused.
- **Actual values are shown.** Each save returns before, intended and actual
  stored values, including Paperless normalization. There is **no durable edit
  history or rollback in M5**. Jobs/history and rollback remain M8/M9.
- **No automatic write retries.** After an uncertain outcome, reload and inspect
  before deciding whether to make another edit.
- **The token stays in the backend.** It is not persisted, logged or sent to the
  browser. Paperless permissions remain authoritative.

See [ADR-0012](docs/decisions/0012-inspector-coordinated-writes-and-external-race.md)
for the concurrency contract and its limitations. Bulk dry-run, bounded job
execution and durable rollback are future capabilities, not M5 guarantees.

## Status

**Early development, M0–M5 implemented.** Explorer uses the M4 Dataset/FilterSet
engine. Click a document title to open its Inspector and edit supported core and
custom fields inline, with explicit save/cancel and conflict handling.
M6 has not started. See [docs/roadmap.md](docs/roadmap.md).

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
can modify documents in your Paperless library using your token.

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
- **Back up your Paperless library.** M5 edits have no durable rollback.
  `/data` holds PaperWrench state; it will hold job history from M8.
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
