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

| Image tag | Reported `X-Version` | Image digest | Complete live suite | Chromium | CI run |
| --- | --- | --- | --- | --- | --- |
| `3.2.1` | Pending new matrix run | Pending | Pending | Pending | Pending |
| `latest` | Pending new matrix run | Pending | Pending | Pending | Pending |

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
