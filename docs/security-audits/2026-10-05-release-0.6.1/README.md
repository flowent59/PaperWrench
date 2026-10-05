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

## Completed evidence

The independently acquired scanner databases have identical content in both
runs: the Grype archive checksum and Trivy database SHA256 match. Trivy schema 2
was updated at `2026-10-05T13:07:51.292695513Z`; its SHA256 is
`d0b30302df0af5b967f542393733c4c17eccd22aa97a5b802f937202e656f784`.
The exact Grype checksum and image IDs are recorded in [comparison.json](comparison.json).

- [Current runtime CI](https://github.com/flowent59/PaperWrench/actions/runs/37322336123):
  all 15 jobs passed, including Docker audit, live Paperless tests and 66 policy tests.
- [Release candidate CI](https://github.com/flowent59/PaperWrench/actions/runs/37322331644):
  all 14 other jobs passed, including live Paperless tests. The Docker audit built
  and scanned successfully but the approved-base policy gate rejected the
  unapproved release fingerprint (exit 2), as required.
- Debian inventory, package file manifests, native probes and deployment controls
  are identical. Python inventory changes only `paperwrench` 0.6.0 to 0.6.1.
- Both scanners report the same **333 occurrences / 80 advisory identities**,
  including exact packages, versions, paths, severities and reported fixes.
- The decision registry Git blob SHA256 remains
  `5a88d3e8b9f69364cf41de8655a9b7997c670b3dd660b275fab6a712a9a2dad6`.
  Its 80 decisions and expiration are unchanged.
- Evaluating these real release reports with the proposed policy passes. This
  archived preview explicitly sets `authoritative: false`: it does not replace
  the approved-base CI gate.

The later release commit `360beeadc3a9b42f400cd01545f7055e54122541`
adds Trivy content checksum recording to the audit tool. It has the same runtime
fingerprint as the version-only source reviewed above. This tool change is also
included in the policy PR and changes no runtime input.

## Reproduction and integrity

[audit-evidence.zip](audit-evidence.zip) retains both raw scanner reports,
metadata, native probes, authoritative gate results, workflow run records,
policy regression results, exact proposed policy and approved registry blob,
comparison and explicitly non-authoritative preview. [SHA256SUMS](SHA256SUMS)
checks the archive and summary; the archive contains checksums for each member.

After extracting the archive, install this repository's development dependencies
and run its archived helper against the policy review checkout:

```sh
python revalidate-release.py --repo /path/to/policy-checkout --baseline baseline --candidate candidate --output reproduced
```

The helper verifies the exact nine version-only source changes, runtime tree
hashes, identical database content, inventories, controls, all normalized
findings, registry checksum and proposed-policy verdict. It needs the source
commits available in the checkout and must run before the policy expiry.

## Required merge order

1. Review and merge [policy PR #110](https://github.com/flowent59/PaperWrench/pull/110).
   This approves this exact additional snapshot, with owner and expiry; it does
   not widen package/advisory decisions or permit future release snapshots.
2. Incorporate approved main into release PR #103 and rerun its complete CI.
   The release gate must then pass against its approved base.
3. Review the green release PR separately before merge or publication. A new
   published digest must be scanned before declaring the registry image fixed.
