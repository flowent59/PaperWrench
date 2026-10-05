# Verification of the recurring image audit gate (#101)

Date: **2026-10-05**. Delivery: [PR #109](https://github.com/flowent59/PaperWrench/pull/109).
[Execution guide](../../image-security-ci.md) · [Policy](../../security-image-policy.md).

The [evidence archive](audit-evidence.zip) preserves original Actions artifacts,
including unfiltered reports, inventories, metadata, checksums and summaries, so
the initial gate verification survives the 90-day artifact retention. Verify it
with the adjacent `SHA256SUMS`; the ZIP contains checksums for every retained file.
[verification.json](verification.json) records the identities and outcomes.

## Real scanner and workflow outcomes

| Case | Result | Evidence |
| --- | --- | --- |
| Corrected runtime built in the PR's Docker job | **Pass**, exit `0`: 333 occurrences, 80 identities, all visible with exact approved decisions expiring October 18 | [`37285922052`](https://github.com/flowent59/PaperWrench/actions/runs/37285922052), ZIP `runtime/` |
| Published `latest`, resolved/pulled by registry digest | **Policy error**, exit `2`: 594 occurrences, 154 identities, no applicable Trixie scope | [`37285362068`](https://github.com/flowent59/PaperWrench/actions/runs/37285362068), ZIP `published/` |
| Early scanner version-command failure during implementation | **Technical error**, exit `2`; the Docker audit step failed, summary/metadata still uploaded | [`37284111920`](https://github.com/flowent59/PaperWrench/actions/runs/37284111920), ZIP `technical-failure/` |

The passing runtime scan uses the PR merge revision recorded in `metadata.json`,
the exact build action image ID and unchanged reviewed runtime tree hash
`76a9cf53894fd5ea4404d237580cb412cfff80bc4062cf34141bb85a8941125a`.
Both scanner exit codes are zero. Grype **0.120.0** and Trivy **0.75.0** archives
were checksum verified; the acquired DB dates were respectively
**2026-10-05 06:45:38 UTC** and **2026-10-05 07:14:49 UTC**. The DBs were frozen
after acquisition for that invocation. The scan has zero blocked occurrences,
not zero vulnerabilities. The raw reports remain unfiltered.

The published image was still Debian 12, OCI revision
`4e7e8ea1ece9542397dc6f3d6621d6dfdd80f8fe`, registry digest
`sha256:01ece472bbf15f6322c80559c576bd3d9aba5582c2e0d46b9efd102d46492265`.
It has a different runtime tree and packages; the approved Debian 13 decisions
cannot apply. The same fresh DB snapshots produced both full raw reports and the
retained blocking summary. Correcting a CI build does not publish an image;
[release PR #103](https://github.com/flowent59/PaperWrench/pull/103) and the existing
release workflow handle publication separately, with runtime-policy revalidation
for any release/version inputs that change.

The scheduled workflow cannot activate before its merge to main. Its scanner
action was therefore exercised on a temporary `audit/issue-101-published-proof`
push branch, with the **already merged #100 commit** explicitly selected as
approved policy. The verification branch changed only the trigger/policy ref
needed for this pre-merge run. The shipped daily/manual workflow checks out main,
resolves that approved revision and invokes the same scanner action. It runs at
05:23 UTC; it will continue to report the old published image until release.

## Blocking regression evidence

`regressions/image-policy-tests.xml` retains **55 passing CI tests** from the real
Docker scan run. `regressions/local-policy-tests-56.xml` additionally covers the
final new-fix-on-another-branch regression. The shipping suite has **56 cases**:

- historical #100 reports pass without removing their 333 occurrences;
- an unexcepted Critical/High/Unknown/unrecognized severity blocks;
- an unfixed High from either Grype or Trivy blocks;
- changed package version/type/path, raised severity and a newly reported fix
  require review, including a new fix when another branch already had one;
- changed runtime inputs, architecture, UID, filesystem controls, installers,
  privileged files, tools, Perl modules or SQLite symbols invalidate decisions;
- scanner nonzero exit, stale/future/missing DB, unsupported report/tool schema,
  identity/layer/distribution mismatch and missing inventory fail technically;
- an expired Low decision blocks globally; malformed ownership, expiry, evidence,
  occurrence or advisory aliases cannot become an acceptance;
- approved first discovery dates enforce the 30-day deadline; missing/future
  initial classification cannot reset or skip review;
- bootstrap loads the reviewed Git object, unapproved registry checksums fail,
  and tampered scanner archives fail before binary execution;
- pass/findings/expiry propagate exits `0`/`1`/`2` through the real runner's result
  writer, keeping replayed raw reports/metadata/summary/checksums;
- an actual CLI early technical error exits `2` and keeps its result/summary.

Fixture tests deliberately replay the retained #100 reports at a frozen historical
time. This is labelled regression evidence, **not a fresh production scan**. The
real runtime and published runs above used fresh databases and real Docker images.
The technical failure's unsupported version flag was corrected before the passing
scan. No finding filter or exception extension was used to make the scan pass.

## Retained functional and permission checks

The existing Docker startup/health/SPA, installed release, TLS/Fernet/Argon2,
populated SQLite restore, non-root/SUID removal and credential checks still run.
Backend/frontend, migrations, release metadata and live Paperless checks remain
in the full CI; the final PR's Actions result reports their delivery status.
Workflow syntax was checked with upstream **actionlint 1.7.12**, and the new Python
scripts/tests passed Ruff and strict mypy.

Fork compatibility was checked structurally: the PR event remains `pull_request`,
permissions are `contents: read`, no publish/login/secrets are added, the public
scanner/DB downloads and local Docker ID need no publication token, and the
approved policy is the PR's base SHA. No separate real fork was created for this
verification. Manual non-main CI also reads `origin/main` policy. Upload steps
use `always()` and were observed retaining artifacts after real failed scans.

The PR keeps `Refs #101` while verification is ongoing and moves to `Closes #101`
only when its full final CI and blocking evidence have been checked. Publication
of a new `latest` image is outside this change; the failing old-image audit is
preserved as an operational finding, not suppressed.
