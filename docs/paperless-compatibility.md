# Paperless compatibility before 0.1.0

The original API observations and M13 acceptance evidence were collected on
Paperless-ngx **3.1.2**. They remain version-scoped in
[the API field notes](paperless-api.md) and
[M13 verification](m13-verification.md). A new run does not rewrite that history.

## Current verification

CI runs the **complete** `tests/backend/live` suite against one disposable
Paperless-ngx `latest` stack with PostgreSQL, Redis, the 17-document Golden
Dataset and a compiled Chromium journey. It exercises real reads, mutations,
recovery and rollback. Every run pulls the image, records its digest, probes
API v10 and records the server's `X-Version` before seeding. That exact version
is passed to the suite's destructive-test guard. A missing version, a non-200
API v10 response, any failed test or any skipped live test fails the job. JUnit
reports are kept as CI artifacts. This follows the newest stable Paperless
release whenever its `latest` tag moves, so compare the recorded digest and
version when diagnosing a new failure.

### Historical 2026-09-25 run

| Image tag | Reported `X-Version` | Image digest | Complete live suite | Chromium | CI job |
| --- | --- | --- | --- | --- | --- |
| `3.2.1` | `3.2.1` | `sha256:5fa76604a81df6945086e0837b14b56543d137e8ce4f311cc5d9ebe907e74e79` | 112 passed, 0 skipped | Passed | [fixed job](https://github.com/flowent59/PaperWrench/actions/runs/36190742655/job/108254979945) |
| `latest` | `3.2.1` | `sha256:5fa76604a81df6945086e0837b14b56543d137e8ce4f311cc5d9ebe907e74e79` | 112 passed, 0 skipped | Passed | [moving-tag job](https://github.com/flowent59/PaperWrench/actions/runs/36190742655/job/108254979948) |

Verified on 2026-09-25 at commit `494df0c` in a
[fully green CI run](https://github.com/flowent59/PaperWrench/actions/runs/36190742655).
Both API v10 probes returned HTTP 200. At this time `latest` resolves to the
**same release and image digest** as the fixed reference, so this run does not
provide evidence for a newer Paperless release. There was no observed API or
business-behaviour incompatibility on 3.2.1.

The first matrix run exposed a stale `3.1.2` assertion in the Chromium test's
own safety probe: 111 backend live tests passed on each tag, then the browser
stopped before its journey. Commit `494df0c` aligned that probe with the
CI-verified expected release; the full rerun above passed. No application
business rule or journey assertion was weakened.

The live suite still requires explicit opt-in, an exact local-host allowlist,
a matching expected server release and at most 500 existing documents. The
Golden Dataset is reseeded after asynchronous ingestion. Runtime compatibility
continues to depend on API negotiation and HTTP status, as described in
[ADR-0008](decisions/0008-api-compatibility-is-decided-by-status-code.md);
`X-Version` here identifies the **test target**.

## Moving-tag policy

Treat a failure after `latest` moves as a possible compatibility change. Use
the recorded digest and reported version to reproduce it, inspect failures and
skips, and review any required adaptation. Do not relax assertions or change
business rules just to obtain a green job. Historical evidence above remains
scoped to the versions and digests shown in its table.
