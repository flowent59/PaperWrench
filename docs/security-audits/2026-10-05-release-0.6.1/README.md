# Release 0.6.1 runtime scope revalidation

Release: [PR #103](https://github.com/flowent59/PaperWrench/pull/103).
Policy: [execution guide](../../image-security-ci.md), approved #100/#101 decisions.

## Candidate and approval boundary

The refreshed release candidate is commit
`03e5e6d5308af1fa8b9961317891ab4c8d9fcdb1`, merged with main
`60e8277377d63edf5d9bdaa965502e0f3c0679a8`. Its runtime Git tree hash is
`57a348818ef99c4ba6c88da640e30df048d8f8ff7ff9ee744793dc5d05c559ee`.
The existing main scope remains
`76a9cf53894fd5ea4404d237580cb412cfff80bc4062cf34141bb85a8941125a`.

The runtime changes are release version fields in backend/pyproject.toml,
backend/src/paperwrench/__init__.py, frontend/package.json/package-lock.json,
and the Compose version reference. Additional release metadata changes are
.env.example, docker-compose.paperless.yml, .release-please-manifest.json and
CHANGELOG.md. Dockerfile, requirement hashes, dependency versions and application
logic do not change. Fresh image inventories and scans must verify the built
result before this scope can be approved.

The original 80 advisory decisions, exact package/version/path matching, owner,
registry checksum and **2026-10-18 00:00 UTC** expiration remain unchanged. The
proposed release scope also expires at that instant. Approving this snapshot
does not accept a new advisory, new package version, changed deployment/native
scope, or automatically extend a decision.

The release PR's current approved-base gate must reject the new fingerprint
until this separate scope review is approved and merged. The policy PR itself
retains the original main fingerprint and runs its CI against the previously
approved main policy. The release branch can then incorporate main and rerun
against the approved additional snapshot. No publication occurs during preparation.

## Evidence status

Fresh release/current runtime scans, inventory comparison, review-preview verdict,
checksums and final functional CI links are being collected before this review
is marked ready. Review previews are not authoritative passing PR gates.
