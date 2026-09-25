# Paperless compatibility before 0.1.0

The original API observations and M13 acceptance evidence were collected on
Paperless-ngx **3.1.2**. They remain version-scoped in
[the API field notes](paperless-api.md) and
[M13 verification](m13-verification.md). A new run does not rewrite that history.

## Current verification

The CI live matrix runs the **same complete** `tests/backend/live` suite against
two independent disposable Compose stacks. Each stack has PostgreSQL, Redis,
the 17-document Golden Dataset and a compiled Chromium journey. It exercises
real reads, mutations, recovery and rollback. Both jobs explicitly pull their
images, record the Paperless image digest, probe API v10 and record the server's
`X-Version` before seeding. The fixed job requires `X-Version: 3.2.1`; the
`latest` job records the version it resolves and passes that exact value to the
suite's destructive-test guard. A missing version, a non-200 API v10 response,
any failed test or any skipped live test fails the job. JUnit reports are kept
as CI artifacts.

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

## Fixed-reference update policy

Keep `3.2.1` fixed until a deliberate compatibility change is reviewed.
To advance it, update the CI matrix and Compose default together, pull the new
image, confirm its reported version, run the full live suite including Chromium,
inspect failures and skips, and record the new digest and results here. Do not
silently advance the reference when `latest` moves. Treat a `latest` failure as
an incompatibility to diagnose; do not relax assertions or change business
rules just to obtain a green job.
