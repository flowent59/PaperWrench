# M13 acceptance audit

Baseline: `5d1a8d9` (M12 merged through PR #26). Scope: issue #16.
Read accepted ADR-0001 through ADR-0015 and existing integration/live tests first.
Later ADR qualifications take precedence over historical implementation plans.

| Acceptance | Existing evidence reused | Demonstrated gap / M13 action |
| --- | --- | --- |
| Find, understand, preview, execute, history, undo | M4–M12 API/UI tests; jobs and rollback live scenarios | No single compiled-browser journey across these modules; add guarded Chromium acceptance |
| Conflicts, ambiguity, interruption, resume, provenance | `test_jobs`, `test_rollback`, live real-process crash boundaries | Re-run unchanged; never infer provenance from equality |
| Custom neighbours and later edits | mutation boundary, inspector/jobs/rollback unit and live regressions | Include later external title in complete journey |
| Pagination at 10k–100k | lazy iterator and 10k staging/adoption bound test | Extend same test to 100k, measure phases and disk, retain bounds |
| Fresh install, upgrade, Docker, compiled routing | Alembic CI/drift, legacy migration and non-root image smoke | Nested URLs resolve Vite `./assets` under the route: use origin-root assets and browser reload regression; test backup restoration |
| Security | token redaction, origin guard, strict payloads, guarded live target | Dependency audit; deployment/auth and backup instructions; assert 3.1.2 in shared live guard |
| Accessibility | labelled controls and component tests | Nested main landmark and unnamed transformation search input; axe plus keyboard journey |
| Release/documentation | milestone API docs and ADRs | README M0 status, historical architecture review, manual API types, release notes/version and upgrade guide |

Roadmap M5/M8 write distinction and M12 static-only scope were already corrected
before this audit; retain them. No new feature or architecture change is needed.

Evidence and final measurements: [M13 verification](m13-verification.md).
