# M13 verification record

Issue #16; baseline `5d1a8d9`, after M12 PR #26. Proposed version **0.1.0**.
See [targeted acceptance audit](m13-audit.md), [release notes](releases/0.1.0.md)
and [operator guide](deployment.md). No M13 merge or release publication is authorized
by this report. CI status is attached to the M13 PR and must be green before merge.

## Acceptance evidence

| Criterion | Evidence / classification |
| --- | --- |
| Explorer → Schemas/Quality → Transformation → Dry Run → Job → History → Rollback | VERIFIED_LIVE: compiled Chromium journey on disposable local Paperless 3.1.2; schema fixture setup, real UI evaluation/drill-down/authoring/confirmation and real backend execution |
| Missing/zero values and later external title | VERIFIED_LIVE: Quality excludes EUR0.00 from required-amount violations; rollback preserves a later third-party title and restores other eligible titles |
| Custom-field neighbours, normalization, conflict, ambiguous writes and real-process interruption | Existing guarded live suite and unit regressions retained; final full-suite result recorded below |
| No false provenance, explicit resume, no overwrite of later edits | VERIFIED_SOURCE: existing Job/rollback unit and mocked integration tests; no execution architecture changed |
| Large target enumeration/adoption/History | VERIFIED_SOURCE: instrumented synthetic HTTP/SQLite test at 10k and 100k, with bounds and measured results below |
| Fresh install, migrations, restoration | VERIFIED_SOURCE: Alembic fresh/drift CI; legacy migration regression; populated M12 backup restored/upgraded twice without loss of schemas/collection membership |
| Production Docker/non-root/compiled SPA | Installed-wheel local smoke passed with UID 10001, read-only root, dropped capabilities, fresh migrations, deep-route assets, unknown API JSON 404 and origin rejection; final-image verification below |
| Security/accessibility/docs | Targeted fixes and scoped audits in [security.md](security.md); installation/upgrade/backup/restore and historical/current documentation aligned |

The browser scenario uses real Golden Dataset documents, restores fixture titles
and never accesses a personal library. Independent live tests already cover
injected response loss after a real PATCH, process exits around durable intent,
PATCH/readback/commit, explicit resume and conservative ambiguity. Injection is
not a claim of a real proxy outage or power-loss durability.

## Measured synthetic performance

Local Windows/Python 3.13, SQLite on local disk; concurrent browser/build activity.
Command: `pytest tests/backend/integration_mocked/test_preview_bounds.py -s`
(also run as part of the complete backend regression). One title operation,
100 documents per mocked upstream page; no real network or write throughput.

| Targets | Pages | Preview | Atomic Job adoption | Last target + operation pages | SQLite + WAL/SHM bytes |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 10,000 | 100 | 16.43 s | 2.76 s | 0.022 s | 24,424,224 |
| 100,000 | 1,000 | 221.59 s | 37.21 s | 0.197 s | 235,864,048 |

Assertions: prior batches reach SQLite before the next GET; at most one prior
100-document page remains referenced; zero retained Documents after enumeration;
at most 200 pending ORM inserts per adoption batch; no new upstream selection
at confirmation; last pages contain exactly 25 rows and the final target.
Garbage collection and database count probes deliberately add measurement overhead.
Disk includes preview plus durable Job rows and SQLite overhead, not just the
serialized-result limit. RSS/peak process memory and sustained write throughput
are NOT_RUN; object-retention bounds are not an RSS measurement.

Limits remain 100,000 targets, 128 MiB serialized preview results, 300-second
build, one active builder, four retained previews, 30-minute expiry, 250-row API
pages and 1–16 mutation workers (default 4). Wider/multi-field documents can hit
byte/time limits earlier. All-or-error cleanup remains tested; no truncated
success. Adoption is a single O(targets × fields) SQLite transaction and can
block writers for the measured tens of seconds. Do not extrapolate to live
100k execution or promise an interactive confirmation latency at the limit.

## Verification execution

- Local backend unit + mocked integration regression: passed; focused new
  origin and M12 backup/restore tests also passed. Ruff and strict mypy passed.
- Local built Chromium journey: **1 passed**, 92.59 s including server startup.
  Axe WCAG 2 A/AA and 2.1 AA checks: zero violations at the tested light-theme
  stages after fixing badge contrast, missing label and nested main landmark.
- Full existing live regression, frontend repeat and final-image audit: in progress
  while preparing the PR; replace with final results before completion.
- All **11 CI gates retained**. Browser acceptance runs inside the live gate;
  compiled route/installed-wheel checks run inside Docker; 100k bounds inside
  mocked integration. No release or auto-merge workflow added.

## Remaining risks and NOT_RUN

ASSUMED: operators pause external writers and maintain a stable library during
pagination. Paperless has no verified atomic conditional PATCH; local locks and
readback do not close the external race or establish exactly-once delivery.
Same-value third-party writes cannot be distinguished by value comparison.

NOT_RUN: live 10k/100k capacity, power-loss/storage corruption, real reverse-proxy
outage, multi-browser/mobile/screen-reader/manual accessibility review, dark-theme
WCAG audit, full penetration test and OS-image CVE scan. Authenticated proxy and
off-host restore procedures are documented; a particular operator's deployment
is not tested. API types remain hand-written without generated drift enforcement.

Moderate Router/Vitest dependency findings remain scoped exceptions documented
in the security review; no claim of a vulnerability-free dependency graph.
Immediate Inspector writes still lack durable rollback; one rollback per original
Job and no rollback-of-rollback remain deliberate MVP constraints. No new
feature, distributed worker or architecture refactor is introduced.
