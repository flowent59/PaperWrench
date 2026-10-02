# Architecture Decision Records

Each record captures one decision, the context that forced it, and the
consequences we accepted. They are written so that a future contributor -
including a future us - can tell the difference between a deliberate constraint
and an accident.

Records are immutable once accepted. A decision that no longer holds is not
edited: a new record supersedes it, and the old one is marked accordingly.

| # | Title | Status |
| --- | --- | --- |
| [0001](0001-single-container-vite-spa-fastapi.md) | Vite SPA + FastAPI in a single container | Accepted |
| [0002](0002-paperless-rest-api-sole-integration-boundary.md) | The Paperless REST API is the sole integration boundary | Accepted |
| [0003](0003-per-document-patch-as-mvp-write-path.md) | Per-document PATCH as the MVP write path | Accepted |
| [0004](0004-safe-custom-field-read-modify-write.md) | Safe custom-field read-modify-write | Accepted |
| [0005](0005-written-value-and-optimistic-conflict-detection.md) | `written_value` and optimistic conflict detection | Accepted |
| [0006](0006-sqlite-durable-job-engine-single-instance.md) | SQLite durable job engine, single instance | Accepted |
| [0007](0007-filterset-compilable-subset.md) | FilterSet compilable subset | Accepted |
| [0008](0008-api-compatibility-is-decided-by-status-code.md) | API compatibility is decided by the status code, not by `X-Api-Version` | Accepted |
| [0009](0009-metadata-registry-cache-not-source-of-truth.md) | Metadata Registry — a TTL cache, never a second source of truth | Accepted |
| [0010](0010-search-is-not-a-filter.md) | Search is a separate primitive, not a FilterSet condition | Accepted |
| [0011](0011-empty-and-missing-are-not-the-same-question.md) | `is missing`, `is null` and `is empty` are three different questions | Accepted |
| [0012](0012-inspector-coordinated-writes-and-external-race.md) | Inspector coordinated writes and the external-writer race | Accepted |
| [0013](0013-expiring-preview-staging-and-confirmation.md) | Expiring preview staging and workflow confirmation | Accepted (M7) |
| [0014](0014-durable-jobs-and-write-provenance.md) | Durable Jobs, target snapshot, provenance and recovery | Accepted (M8); qualifies 0003/0005/0006/0012/0013 |
| [0015](0015-safe-rollback-jobs.md) | Safe linked rollback Jobs, review and grouped conflicts | Accepted (M9) |
| [0018](0018-per-user-interface-locale.md) | Interface locale is a per-user presentation preference | Accepted |
| [0019](0019-remembered-credentials.md) | Optional local accounts and encrypted remembered credentials | Accepted; qualifies 0016 |

## Format

    # ADR-NNNN: Title
    ## Status
    ## Context
    ## Decision
    ## Consequences
    ## Alternatives considered
