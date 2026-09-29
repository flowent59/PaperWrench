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
records that are missing a date, and reviewing safe rollback of proven bulk
writes. Duplicate detection remains outside the MVP.

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
  history or rollback for Inspector edits**. M8 bulk Jobs have durable History;
  M9 adds safe, explicitly confirmed rollback of proven Job writes.
- **No automatic write retries.** After an uncertain outcome, reload and inspect
  before deciding whether to make another edit.
- **The token stays in the backend.** It is not persisted, logged or sent to the
  browser. Paperless permissions remain authoritative.

See [ADR-0012](docs/decisions/0012-inspector-coordinated-writes-and-external-race.md)
for the concurrency contract and its limitations. M8 adds explicitly confirmed
bulk Jobs, exact durable targets and paginated History. PATCHes are followed by
a GET; uncertain outcomes stay **AMBIGUOUS**, without invented write provenance
or automatic replay. Restart requires explicit resume of unsent targets.
See [ADR-0014](docs/decisions/0014-durable-jobs-and-write-provenance.md).

## Status

Schemas, Data Quality and static Collections complete the workflow. Read the
[release overview](docs/releases/0.1.0.md), [generated changelog](CHANGELOG.md),
[installation/upgrade/backup guide](docs/deployment.md)
and [M13 evidence and limits](docs/m13-verification.md). The reference Compose
file pulls `ghcr.io/flowent59/paperwrench:0.1.0` after the generated release PR
is merged and the package is made public. See [the release process](docs/releasing.md).

**MVP 0.1.0.** Compatibility is verified
with Paperless-ngx 3.2.1; its `latest` tag resolved to the same image digest in
the recorded CI run. Explorer uses the Dataset/FilterSet
engine and can send selected IDs or all matching documents to Transformations.
Dry Run shows paginated before/intended values and changed/unchanged/error
counts without writing to Paperless. **Confirm and apply** atomically creates a
durable Job from the reviewed targets. History shows progress, conflicts, errors,
ambiguous outcomes and before/intended/verified written values. Concurrency defaults
to 4 (maximum 16); losing the runtime lock stops new Job sends. History offers
rollback preview and confirmation: changed values conflict, uncertain writes are
never automatically restored. One linked rollback Job is allowed per original.
The external GET/PATCH race remains; pause other writers. No user cancellation yet. Single-document Inspector edits remain available.
See [docs/roadmap.md](docs/roadmap.md), [preview API](docs/preview-api.md) and
[Job API](docs/job-api.md) and [rollback API](docs/rollback-api.md).

## Quick start

Requires an existing Paperless-ngx Docker stack. Each user opens **My Profile**
from their Paperless user menu, creates or regenerates their **API Auth Token**,
then signs in to PaperWrench with that individual token. This local-network
setup needs no domain, TLS certificate or reverse proxy.

```bash
git clone https://github.com/flowent59/PaperWrench.git
cd PaperWrench
PAPERLESS_DOCKER_NETWORK=paperless_default docker compose -f docker-compose.paperless.yml pull
PAPERLESS_DOCKER_NETWORK=paperless_default docker compose -f docker-compose.paperless.yml up -d
```

Replace `paperless_default` with the Docker network used by your Paperless
service. Open `http://IP_DU_SERVEUR:8000` (or set `PAPERWRENCH_HTTP_PORT`).
For a differently named Paperless service, set `PAPERLESS_SERVICE` too.
See the [installation and v0.1-to-v0.2 migration guide](docs/deployment.md) for
exact steps, session settings, permission behavior and troubleshooting.
These commands work once the versioned GHCR image is published. Until then,
the guide gives the local-build override for a reviewed checkout.

See [Threat model](#threat-model) before making the port reachable beyond a
trusted LAN.

## Development

```bash
make install          # backend venv + frontend deps
make check            # lint, typecheck and tests, exactly as CI runs them
make dev-backend      # API on :8000
make dev-frontend     # Vite dev server on :5173
```

Never develop against your real library. A disposable, pre-seeded
Paperless-ngx 3.2.1 instance is one command away:

```bash
make dev-paperless-up     # Paperless-ngx 3.2.1 on :8010
make dev-paperless-seed   # reference dataset, French metadata included
```

Full instructions in [docs/development.md](docs/development.md).

## Threat model

Read this before deciding where to run PaperWrench.

PaperWrench authenticates each user by validating their own Paperless API token.
The resulting session is held in an `HttpOnly`, `SameSite=Strict` cookie and
state-changing requests require a per-session CSRF token. On a trusted HTTP-only
LAN the token is still encrypted in transit by neither application; use HTTPS
when the network is not fully trusted.

What this means concretely:

- **Do not expose PaperWrench over plaintext internet.** Put it behind HTTPS,
  restrict direct backend access, and forward the original scheme correctly.
- **The token is as powerful as the user it belongs to.** Create a dedicated
  Paperless user with only the permissions you are willing to delegate, rather
  than using an administrator token.
- **Back up your Paperless library.** M5 edits have no durable rollback.
  `/data` holds durable Job history and write provenance. M8 cannot restore data.
- **Keep TLS verification on for HTTPS connections.**
  `PAPERWRENCH_PAPERLESS_VERIFY_SSL=false` exists for self-signed certificates
  on a LAN, and it removes protection against an active network attacker.

What PaperWrench does *not* do: after login it never sends your token back to the
browser, never writes it to its database, never includes it in logs (log output is scrubbed at
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
pull request; CI additionally runs guarded live/browser, migration and Docker gates.
UI changes must update the complete English and French catalogues. See the
[translation contribution guide](docs/i18n.md) for the JSON layout, placeholders,
plural forms, adding a locale, and the community review workflow.

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
