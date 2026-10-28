# Published 0.6.1 image: 2026-10-27 review

Source: [scheduled audit run 40490458077](https://github.com/flowent59/PaperWrench/actions/runs/40490458077), including the retained `published-image-security-40490458077` artifact. The scan resolved `ghcr.io/flowent59/paperwrench:latest` to registry digest `sha256:dceb3501206712c68b7b29887da009dc8e7bd190d4b7adfc57da17f07922470b`, image ID `sha256:7b7bce3b51f8cc90098a874d5ce7612f8dacc0865126b1abf57f3eac9201045b`, and OCI revision `fb4ef9f7c4c78e73742ae9b210128c787cffc651` (`v0.6.1`). The report is for the published Linux amd64 image and the audit's hardened probe container, not an operator's deployment.

Both scanners completed successfully with 2026-10-27 databases. Grype 0.120.0 reported 167 occurrences and Trivy 0.75.0 reported 168. Together they reported 335 scanner/package occurrences and 82 raw advisory identifiers. The audit returned exit 2, `technical-error`, because the approved release runtime snapshot expired on 2026-10-18. The 80 individual decisions also expired on that date. This result is **not** a clean vulnerability assessment, and neither the scheduled job nor an open follow-up issue renews the decisions.

Comparison against the approved register found no changed exact package/version/scanner-path occurrence among the previously recognized findings. `CVE-2026-19487` was absent. Four occurrences belong to three new advisory identifiers:

| Identifier | Affected installed package | Scanner evidence | Supported correction reported |
| --- | --- | --- | --- |
| [GHSA-6q48-6c7w-9gw2](https://github.com/advisories/GHSA-6q48-6c7w-9gw2) | `certifi 2026.7.22` | Grype and Trivy, Critical | `2026.10.18`; upstream recommends `2026.10.19` or later |
| [DSA-6572-1](https://security-tracker.debian.org/tracker/DSA-6572-1) | `ca-certificates 20250419` | Trivy, Unknown | Debian 13 security: `20250419+deb13u1` |
| [DSA-6549-1](https://security-tracker.debian.org/tracker/DSA-6549-1) | `liblzma5 5.8.1-1+deb13u1` | Trivy, Unknown | Debian 13 security: `5.8.1-1+deb13u2` |

Trivy also newly reports `5.8.1-1+deb13u2` as a correction for the existing `TEMP-1147318-639065` occurrence. Grype lists `3.15.0rc3` for `CVE-2026-12345`; this prerelease of a different Python branch is not evidence of a supported 3.11 fix. Current upstream and Debian evidence must be checked before renewing any of the other residual decisions.

## Remediation in progress

This review accompanies a source change that pins `certifi 2026.10.23` and requests the supported Debian security upgrades for `ca-certificates` and `liblzma5` during the image build. A local Linux amd64 Docker build completed on 2026-10-27. A non-root, network-disabled, read-only container with dropped capabilities reported UID 10001, `certifi 2026.10.23`, `ca-certificates 20250419+deb13u1`, `liblzma5 5.8.1-1+deb13u2` and `libpcre2-8-0 10.46-1~deb13u3`. This is package-installation evidence; the exact-source CI scan is recorded below. The old published image remains affected until a corrected release is built and published.

A separate full frontend dependency-tree `npm audit` initially found seven
entries (three High and four Moderate). Compatible lockfile updates, including
Vite 6.4.5, Zod 4.6.7 and Vitest 5.0.5, reduced that report to zero known
findings. This is a build/development dependency audit and does not replace the
runtime image scan. The updated lockfile passed the CI frontend test/build gates.

## Candidate CI evidence

**Follow-up on 2026-10-27:** [Python 3.14.10](https://www.python.org/downloads/release/python-31410/) was published on 2026-10-25 with security corrections, including rejection of compromised eMudhra intermediate CA keys by `ssl`. The official Linux amd64 `python:3.14-slim-trixie` image identifies itself as `3.14.10-slim-trixie` (OCI revision `01dcc3d31fcd879f9af36e097bd96328a457f4bd`). The source change migrates both Python stages from 3.11.17 to this image at index digest `sha256:82abc9f53600280b0757126f83c5a7d47088592d2acb6b4c0b9e97f76865508c`. The candidate scan below predates this base update and does not describe the final candidate. Review the subsequent CI artifact for exact installed versions, findings, and controls before assessing residual decisions. The PaperWrench release and review are scoped to Linux amd64.

[PR #113 CI run 40579067334](https://github.com/flowent59/PaperWrench/actions/runs/40579067334)
built the changed source and retained the `image-security-audit` artifact. Its
image/config ID is `sha256:8da9dd971af3ee1cdba7c5e858440e27de3ab0a121238ab16be0b870b9930d43`,
OCI/source revision `75b77b3ddc33fa8cb80928daed193b43c046056f` (the PR
merge checkout), and runtime tree hash
`165ecc412b1460a54b1a9386cf8cfd5210adc26ba0fa387f53bedbbaf9feb037`.
The Docker functional checks passed: startup, installed release, TLS/credentials,
SQLite restore, non-root user, privilege-bit and token checks. The exact image
audit still returned exit 2, `Expired reviewed runtime scope`; this is the
correct policy outcome and is advisory for the Docker job.

The candidate's unfiltered Grype and Trivy reports contain **330 occurrences
and 78 raw advisory identifiers**. Relative to the published-image reports
above, there are no added normalized occurrences and five absent ones: the
two Critical `certifi` occurrences, the two new Debian DSA occurrences, and
the previous `liblzma5` TEMP occurrence. One Unknown-severity occurrence remains
(`CVE-2026-82560` on `perl-base`), alongside 99 High-severity scanner
occurrences. The Grype DB build timestamp matches between these two runs;
Trivy refreshed from `2026-10-27T07:13:33Z` to `2026-10-27T19:17:44Z`.
Consequently this is an observed report comparison, **not** a controlled
same-database before/after test. Package versions and the corresponding
upstream/Debian corrections support the specific fixes, but the remaining
decisions still require a fresh individual review.

The existing policy must continue to report an error while its reviewed runtime scope and decisions are expired. A new dated decision register needs individual reassessments, exact occurrences, owner, expiry, evidence, and review through a separate policy PR. No exception is extended or scanner finding suppressed by this report.

## Python 3.14.10 migration scan

[PR #113 CI run 40584467798](https://github.com/flowent59/PaperWrench/actions/runs/40584467798) built the Linux amd64 candidate after the Python migration. All 15 CI jobs passed, including the live Paperless/Chromium journey. The Docker functional job checked startup/migrations, installed wheel/SPA, native TLS and credentials, SQLite restore, non-root execution, privilege bits, and public response checks. The exact-source image/config ID is `sha256:c25323a45d6ca877912aea06d807992b42d23235f18b42bde5d28dad6917c883`, OCI/source revision `c293803799465145c2dabd3f2b33ff7b13a2799e` (PR merge checkout), and runtime tree hash `d91211d9044f4cc2dae7813056ef41f5f18c7f3107497c9da201d8460a17dc52`. Its retained `image-security-audit` artifact includes both raw scanner reports, inventories, probe and database metadata. The audit still correctly reports `technical-error` because the reviewed scope expired.

This scan and the preceding 3.11 candidate scan used the **same** Grype database build (`2026-10-27T06:30:41Z`) and Trivy database update (`2026-10-27T19:17:44Z`). On the same normalized scanner basis, occurrences fell from **330 to 325** and raw advisory identifiers from **78 to 73**, with no new advisory identifier or OS-package finding. The Python 3.14 image has 99 High, 108 Medium, 71 Low, 46 Negligible and one Unknown scanner occurrences. The five absent Python identities are `CVE-2025-12781`, `CVE-2025-15366`, `CVE-2026-3446`, `CVE-2026-3479` and `CVE-2026-6019`. This is a scanner-result reduction; the two remaining Python findings need their own code-level assessment.

`CVE-2025-15367` and `CVE-2026-12345` remain reported by Grype against the new `python 3.14.10` binary, so the two old exact-version decisions cannot match. An installed-code probe confirms `poplib.POP3._putcmd` has no control-character rejection in 3.14.10. The [Debian tracker](https://security-tracker.debian.org/tracker/CVE-2025-15367) and [CPython issue](https://github.com/python/cpython/issues/143923) describe the first issue. The installed `tempfile.TemporaryDirectory._rmtree` code and [CPython issue #157579](https://github.com/python/cpython/issues/157579) need re-review for the second. Neither finding should be marked fixed solely from the branch upgrade. The other 323 occurrences match the initial register's exact scanner/package/version/path records with no severity increase, but their decisions expired and their runtime scope changed. A separate reviewed policy update remains required.
