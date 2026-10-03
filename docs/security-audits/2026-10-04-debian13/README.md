# Debian 13 migration evidence — 2026-10-04

Implements [#98](https://github.com/flowent59/PaperWrench/issues/98), delivered in
[PR #102](https://github.com/flowent59/PaperWrench/pull/102). This is a Linux amd64
audit of the installed runtime, with a separate frontend builder dependency audit.
The candidate is a build for verification; it has not been published to GHCR.

## Image identities and scope

The published baseline was pulled by immutable reference:

```text
ghcr.io/flowent59/paperwrench@sha256:01ece472bbf15f6322c80559c576bd3d9aba5582c2e0d46b9efd102d46492265
```

Its OCI revision is `4e7e8ea1ece9542397dc6f3d6621d6dfdd80f8fe`, and its Docker
image/config ID on the audit runner is
`sha256:a33a15ee0e9a50fbe7be2c5d9e52b8c9f5dfc29b201f26a3fb071d10d27f538f`.
This is distinct from the starting checkout, `8df8315589737e43fd9b1ad082d0653088055a51`:
the intervening changes affect README/deployment documentation only.

The final candidate was built from `528890161c5d1837cc99bd445d4516eb5160be4a`,
recorded in its OCI revision label, and scanned by Docker image/config ID:

```text
sha256:feee9d2bcda60bda5083b7ae170a6a2d4d25dfecf9a2919a74381d6db2ce5503
```

These config IDs identify the images loaded into the runner's Docker daemon;
they are not registry pull references. The raw inspect documents and inventories
are archived below. Later PR commits add documentation/evidence only; they do
not change the Dockerfile, backend, frontend or lockfiles used by this audit.
All 40 installed Python distribution versions are identical between the two
images, including PaperWrench 0.6.0. The Python interpreter patch version changes.

Verified multi-platform base index digests used by the Dockerfile:

| Stage | Base | SHA-256 index digest |
| --- | --- | --- |
| Frontend | `node:22-trixie-slim` | `b26b04c123d9ff8ab646ceb18b9d75a1173acf64b9a401094b906d27b29338d4` |
| Backend/runtime | `python:3.11-slim-trixie` | `bab1b7ef4b450c81002278d035eff85ebe394ae94df904f7a3ba14f7e16e487b` |

The runtime also upgrades `libpcre2-8-0` from the configured Debian repositories.
The first candidate contained `10.46-1~deb13u2`, reported for CVE-2026-103111;
APT confirmed `10.46-1~deb13u3` in `trixie-security`. The final inventory contains
that update and neither scanner reports this finding. The initial Grype report
and APT check are included under `pre-pcre2-update/` in the archive.
APT repository contents can change; the installed inventory records this build's
actual versions rather than claiming the base digest alone fixes all build inputs.

## Scanner snapshots and results

[Audit run 37162317470](https://github.com/flowent59/PaperWrench/actions/runs/37162317470)
built, tested and scanned both immutable image IDs. Scans completed at
`2026-10-03T23:39:04Z` (4 October in Europe/Paris).

- Grype **0.120.0**, Syft **1.54.0**; DB schema **v6.1.10**, built
  `2026-10-03T06:31:58Z`. DB archive SHA-256:
  `397565327faac36f757903f87e31ac403a251bed21c0f0ec5b87f912c5143ab6`.
  Automatic DB updates were disabled for both scans; the report DB descriptors
  are identical.
- Trivy **0.75.0**; DB schema **2**, updated
  `2026-10-03T19:02:38.737369755Z`. Both scans used the same downloaded cache with
  DB/Java DB updates disabled, offline analysis, Docker image source and the
  vulnerability scanner only.

| Scanner/image | Critical | High | Medium | Low | Negligible | Unknown | Total |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Grype / Bookworm | 10 | 73 | 101 | 17 | 80 | 16 | 297 |
| Grype / Trixie + PCRE2 update | 0 | 56 | 56 | 10 | 46 | 0 | 168 |
| Trivy / Bookworm | 5 | 63 | 119 | 107 | — | 2 | 296 |
| Trivy / Trixie + PCRE2 update | 0 | 49 | 62 | 60 | — | 2 | 173 |

Totals are scanner/package/advisory entries, not distinct CVEs. Do not add
scanner totals or equate their severity taxonomies. The baseline counts reproduce
the user report of 3 October; that report did not record image or scanner metadata.

The [complete comparison](findings.csv) contains all 630 scanner/package/advisory
pairs from the union of the reports, with versions, severities, fix status, paths
when supplied, deltas and follow-up issue numbers. Only Debian's `t64` package
renames are normalized; CVE/GHSA identifiers are not merged across scanners.

| Scanner | Retained pairs | No longer reported | Newly reported on Trixie |
| --- | ---: | ---: | ---: |
| Grype | 151 | 146 | 17 |
| Trivy | 153 | 143 | 20 |

“No longer reported” is an observed scan delta; it alone does not prove that a
component was fixed rather than removed or matched differently. The inventories
show which packages remain installed. Newly reported and retained findings are
included in the residual review, even though the total decreases.

## Critical findings and runtime versions

All ten baseline Grype critical package/advisory entries disappear. The affected
packages remain installed with the following versions:

| Packages | Bookworm | Final Trixie | Baseline critical CVEs no longer reported |
| --- | --- | --- | --- |
| `libc-bin`, `libc6` | `2.36-9+deb12u14` | `2.41-12+deb13u4` | `CVE-2026-5450` |
| `perl-base` | `5.36.0-7+deb12u3` | `5.40.1-6+deb13u1` | `CVE-2026-8376`, `CVE-2026-13221`, `CVE-2026-42496`, `CVE-2026-12087`, `CVE-2026-57433` |
| `libsqlite3-0` | `3.40.1-2+deb12u2` | `3.46.1-7+deb13u2` | `CVE-2025-7458` |
| `libssl3` → `libssl3t64`, `openssl` | `3.0.20-1~deb12u2` | `3.5.7-1~deb13u3` | `CVE-2026-75803` |

OpenSSL had an available Bookworm fix (`3.0.22-1~deb12u1`) in the baseline
Grype DB, so its disappearance is not evidence that this CVE required Debian 13.

| Runtime probe | Bookworm | Trixie |
| --- | --- | --- |
| Debian | 12 / Bookworm | 13 / Trixie |
| Python | 3.11.16 | 3.11.17 |
| SQLite used by Python | 3.40.1 | 3.46.1 |
| OS OpenSSL used by Python `ssl` | 3.0.20 | 3.5.7 |

The native credential/TLS probe also reports glibc 2.41 and the separately bundled
cryptography OpenSSL 4.0.1. Updating Debian OpenSSL does not update that wheel.

## Verification

The [PR CI checks](https://github.com/flowent59/PaperWrench/pull/102/checks) cover
backend lint/type checking, unit/integration/live tests, migrations, multi-user
security, frontend lint/types/tests/translations/build, release metadata and Docker.
The Docker gate retains startup/health, installed wheel and compiled nested SPA
routes, auth/origin boundaries, non-root UID, read-only root, dropped capabilities
and credential-leak checks. The existing healthcheck also passed locally.

Additional installed-runtime checks pass:

- `pip check`: no broken requirements, without installing test dependencies.
- `scripts/smoke_native_runtime.py`: verified local TLS handshake and encrypted
  round trip using Python's OS OpenSSL; EC certificate generation, production
  token encryption/decryption and Argon2 correct/wrong-password checks.
- CI streams the existing populated SQLite migration fixture into the read-only
  runtime and checks restore, repeated upgrade, integrity and foreign keys.
- The audit runner creates a disposable populated M12 fixture with the published
  Bookworm image, uses SQLite's backup API, then opens/upgrades that backup twice
  in the exact scanned Trixie image. Collection `Vacations`, document membership
  `42`, schema `Amounts` and legacy `owner_id=None` survive; Alembic check,
  `integrity_check` and `foreign_key_check` pass. No real user database is involved.

## Remaining work

- [#99](https://github.com/flowent59/PaperWrench/issues/99): cryptography 49.0.0
  still has `GHSA-g6cj-pr64-35w5` / `CVE-2026-69247`. Trivy's Python results remain
  seven entries, including msgpack 1.1.2, setuptools 70.3.0 and urllib3 2.7.0.
  Those last three entries are marked `AnalyzedBy: sbom`, with no package path;
  the installed distribution inventory contains setuptools 84.0.0 and no
  msgpack/urllib3. Their origin and any embedded copies still need verification.
  They are not dismissed or fixed by installing global packages.
- The separate Node 22 frontend builder `npm audit --json` reports **six high
  package entries, zero critical**, across four unique advisories:
  `GHSA-q2hr-2g5m-vwhr`, `GHSA-qhr7-859c-m2p7`, `GHSA-6j4f-fj2g-mc7p`
  (brace-expansion), and `GHSA-vfj7-8cjw-p6xm` (braces). Entries propagate to
  chokidar, micromatch, fast-glob and tailwindcss. #99 covers this new snapshot;
  `node_modules` is not copied into the runtime image.
- [#100](https://github.com/flowent59/PaperWrench/issues/100): retained/new Debian
  and CPython findings need applicability review. No Debian match in the final
  Grype report has an available fixed version in this DB snapshot. For example,
  util-linux `CVE-2026-76642`, glibc `CVE-2026-5435` and Perl `CVE-2026-82560`
  remain. “wont-fix” is a vendor status, not a project risk acceptance.
- [#101](https://github.com/flowent59/PaperWrench/issues/101): recurring scans and
  final policy. This one-off audit workflow is stored as evidence and is not
  installed in the PR's regular CI.

## Raw evidence and reproduction

[audit-evidence.zip](audit-evidence.zip) preserves all original final reports,
image inspections, package inventories, tool/DB metadata, native and restore
outputs, build-input checksums, the initial PCRE2 finding, frontend audit and the
one-off workflow. [SHA256SUMS](SHA256SUMS) covers the ZIP and comparison CSV.
The ZIP is committed so the evidence survives the runner artifact's expiry.

Using Python 3, extract and regenerate the comparison without network access:

```sh
python -m zipfile -e audit-evidence.zip extracted
cd extracted
python evidence/compare-audits.py audit .
```

On Linux, `sha256sum -c audit/reports.sha256` verifies the original JSON reports.
The archived workflow records the exact build and scanner commands, including
verified scanner binary hashes and DB update controls. To reproduce a new audit,
build the recorded source with `--platform linux/amd64` and its `VCS_REF`, pull
the baseline by its registry digest, and scan each resulting Docker ID. Use the
recorded DB snapshot for a historical comparison; newer DBs can change results.
The database files themselves are not committed. These checks establish the
observed migration result, not absence of all vulnerabilities or exploitability.
