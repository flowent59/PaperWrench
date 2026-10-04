# Installed dependency corrections — 2026-10-04

Implements [#99](https://github.com/flowent59/PaperWrench/issues/99), delivered in
[PR #105](https://github.com/flowent59/PaperWrench/pull/105), after the Debian 13
migration in #98. The separate unpatched frontend build chain is tracked in
[#104](https://github.com/flowent59/PaperWrench/issues/104), as allowed by #99's
npm follow-up criterion. Neither verification image is published to GHCR.

## Inputs and immutable identities

[Audit run 37198401555](https://github.com/flowent59/PaperWrench/actions/runs/37198401555)
builds both images on Linux amd64 and scans their Docker image/config IDs:

| Role | Source commit / OCI revision | Docker image/config ID |
| --- | --- | --- |
| Debian 13 baseline, after #98 | `14a4dc4be55cbb365b9b2e5ee74917f112194f1b` | `sha256:172351567c035684a53a5f663db00605c51760faf291688027a57c31ece9e535` |
| Dependency corrections | `a4e9c6d3466e7e557c1140270a3692f47b36e20c` | `sha256:35118403b7a3f7d37b2c897c8d0b5b5b9cfa5b9e7988430626dbbdebe6e185fd` |

These IDs identify images loaded into the audit runner, not registry pull
references. The baseline is rebuilt from the merged Debian 13 source; the
published `latest` image was still the Bookworm image identified in the
[earlier audit](../2026-10-04-debian13/README.md). This comparison isolates #99
from the distribution migration. Subsequent PR commits add documentation/evidence
only and do not change the audited Dockerfile, backend, frontend or lockfiles.

Both images use Python 3.11.17, Debian 13, SQLite 3.46.1 and OS OpenSSL 3.5.7,
including the PCRE2 update from #98. Their Debian package inventories are byte
identical. The Python inventories differ only as follows:

| Distribution | Baseline | Candidate |
| --- | --- | --- |
| cryptography | 49.0.0 | 50.0.2 |
| pip | 26.2.1 | removed |
| setuptools | 84.0.0 | removed |
| wheel | 0.46.3 | removed |

All 37 candidate distributions match the 36 exact lockfile pins plus the built
PaperWrench 0.6.0 wheel. The minimum cryptography version in project metadata is
also 50.0.2. The [upstream changelog](https://cryptography.io/en/stable/changelog/)
confirms the PKCS#7 fix in 50.0.0, OpenSSL 4.0.3 in the 50.0.2 wheels, and the
50.x changes were reviewed against the app's Fernet and native TLS/certificate
uses. Python 3.11 remains supported by this release's package metadata.

## Origin and removal of the embedded copies

Trivy's apparent extra setuptools/msgpack/urllib3 packages were real vendored
copies inside pip, rather than the global distributions in `requirements.lock`:

| Trivy package/version | Confirmed baseline path / owner |
| --- | --- |
| msgpack 1.1.2 | `/usr/local/lib/python3.11/site-packages/pip/_vendor/msgpack` |
| urllib3 2.7.0 | `/usr/local/lib/python3.11/site-packages/pip/_vendor/urllib3` |
| setuptools 70.3.0 | `/usr/local/lib/python3.11/site-packages/pip/_vendor/pkg_resources`; version declared in `pip/_vendor/vendor.txt` |

The global setuptools 84.0.0 distribution did not replace pip's legacy copy.
PyPI's stable pip release was still 26.2.1 at this review and retained these
versions. No application imports or active default runtime requirements require
pip, setuptools or wheel. The archived provenance reports contain the vendor
manifest, copy paths, import availability and checked dependency declarations.

The build installs exact runtime dependencies, installs the app wheel with
`--no-deps`, removes setuptools/wheel, then **requires `pip check` to pass** before
removing pip. Its output is captured in `candidate-pip-check-build.txt` and the
full build log. Removing pip removes its owned vendor tree and metadata; no
libraries are injected globally to disguise a vendored finding. The final
filesystem has no matching vendor manifests, msgpack/urllib3/pkg_resources copy
directories or importable pip/setuptools/wheel/pkg_resources modules.
CI independently verifies the module absence and installed application behavior.

An additional inspection found ensurepip's bootstrap wheels outside site-packages:
`setuptools-79.0.1-py3-none-any.whl` and `pip-24.0-py3-none-any.whl`. The latter
still embeds older msgpack, urllib3 and pkg_resources copies. These archives are
outside the installed distribution metadata used by pip-audit and were not
reported as Python findings by these image scans. The runtime also removes this
unused stdlib bootstrap module and both wheels. The provenance report lists the
baseline wheel paths and embedded vendor manifests; the candidate has neither
bootstrap wheels nor an importable ensurepip module. CI checks that absence too.

The image now has no installer. Change dependencies by rebuilding it; developer
environments continue to use pip. Installation tools remain available in build
stages and the separate auditing environment.

## Audit snapshots and before/after results

Scans completed at `2026-10-04T11:23:56Z`. Each scanner uses one cached DB for
both images; Grype's two DB descriptors are identical. No vulnerability IDs
were ignored and no SBOMs were removed to mask findings.

- Grype **0.120.0**, Syft **1.54.0**, DB schema **v6.1.10**, built
  `2026-10-04T08:11:47Z`, DB archive SHA-256
  `2bd87418b8877e75d07351ef8daa9636ef98d69e09e0f69a15ae9c191e895d39`.
  Auto-update disabled during the comparison.
- Trivy **0.75.0**, DB schema **2**, updated
  `2026-10-04T08:52:32.606987838Z`, downloaded once; both scans use the same cache,
  disabled DB/Java DB updates, offline analysis and Docker source.
- pip-audit **2.10.1**, PyPI advisory service, scans copied installed metadata
  using `--path` from a separate Python 3.11 venv. It adds no dependencies to
  either image. PaperWrench 0.6.0 is the one skipped distribution, because it is
  not on PyPI; all 36 candidate third-party distributions are audited.

| Scanner | Total entries before → after | Python entries before → after | Critical before → after | High before → after |
| --- | ---: | ---: | ---: | ---: |
| Grype | 168 → 167 | 1 → 0 | 0 → 0 | 56 → 55 |
| Trivy | 173 → 166 | 7 → 0 | 0 → 0 | 49 → 44 |

The Python findings removed from the installed image are:

- cryptography: `GHSA-g6cj-pr64-35w5` / `CVE-2026-69247`, fixed by upgrading.
- pip's msgpack copy: `GHSA-6v7p-g79w-8964`, removed with pip.
- pip's setuptools copy: `CVE-2025-47273` and `CVE-2026-59890`, removed with pip.
- pip's urllib3 copy: `CVE-2026-97687`, `CVE-2026-97688` and
  `CVE-2026-97689`, removed with pip.

pip-audit reports **zero known vulnerabilities** for the candidate. Its baseline
JSON contains the same cryptography `PYSEC-2026-3552` entry twice with CVE/GHSA
aliases; the baseline's two raw entries represent one advisory, not two distinct
vulnerabilities. pip-audit alone does not establish the status of vendored copies;
the directory inspection and Trivy comparison provide that additional evidence.

The [complete CSV](findings.csv) contains all 341 scanner/package/advisory pairs,
with installed versions, reported fix statuses, paths when supplied and follow-up
issue numbers. 333 pairs are retained, eight no longer reported, none newly
reported. Debian `t64` renames and multiarch inventory suffixes are handled by the
archived comparison script; identifiers are not merged across scanners.
Totals are package/advisory entries, not distinct CVEs; do not add scanner totals.

## Frontend findings and follow-up

Both brace-expansion copies were updated within existing dependency ranges:
2.1.4 → **2.1.7** under Redocly and 5.0.9 → **5.0.12** at the root. Their three
advisories (`GHSA-q2hr-2g5m-vwhr`, `GHSA-qhr7-859c-m2p7`,
`GHSA-6j4f-fj2g-mc7p`) disappear. No overrides or framework major upgrades were
used; package changes are limited to these two entries, with unchanged platform
selectors preserved.

`npm audit` changes from **six to five High package entries**, zero Critical.
The remaining single advisory is
[GHSA-vfj7-8cjw-p6xm / CVE-2026-93687](https://github.com/advisories/GHSA-vfj7-8cjw-p6xm),
with no patched braces release at this review. It propagates through braces,
chokidar, micromatch, fast-glob and tailwindcss. [#104](https://github.com/flowent59/PaperWrench/issues/104)
records exact chains, compatibility choices and UI/build/test acceptance criteria
for replacing that chain. npm's suggested Tailwind major migration was not applied
automatically. These Node dependencies are build tooling; `node_modules` is not
copied into the runtime image. This is a scope distinction, not a finding exclusion.

The retained 160 Debian plus seven CPython Grype entries, and 166 Debian Trivy
entries, remain for [#100](https://github.com/flowent59/PaperWrench/issues/100).
Recurring scans/final policy remain in [#101](https://github.com/flowent59/PaperWrench/issues/101).

## Verification and durable evidence

The [PR CI checks](https://github.com/flowent59/PaperWrench/pull/105/checks) cover
backend lint/types/unit/integration/live tests, migrations, multi-user security,
frontend lint/types/tests/translations/build, release metadata and Docker.
The installed Docker gate retains startup, health, nested SPA routes,
authentication/origin/credential boundaries, non-root user, read-only filesystem,
dropped capabilities, populated SQLite restore and repeatable migrations.

The exact scanned candidate also passes the native TLS, Fernet and Argon2 probe
under the same container restrictions. OS OpenSSL remains 3.5.7; cryptography's
bundled OpenSSL changes to 4.0.3. A separate upgrade probe encrypts a synthetic
owner-bound credential with the baseline's cryptography 49.0.0 and decrypts it
using the candidate's 50.0.2 through the production CredentialVault. No real
credential or user database is used, and the fixture key is not archived.

[audit-evidence.zip](audit-evidence.zip) retains raw Grype/Trivy/pip-audit/npm
JSON, installed inventories, inspections, source and build-input hashes, DB/tool
metadata, provenance/native/upgrade results, build/pip-check output and the
one-off workflow. [SHA256SUMS](SHA256SUMS) covers the archive and CSV. The archive
is committed so evidence survives the GitHub artifact's expiry; the one-off
workflow is not added to the PR's regular CI.

Regenerate the comparison offline with Python 3:

```sh
python -m zipfile -e audit-evidence.zip extracted
cd extracted
python evidence/compare-audits.py audit .
```

On Linux, `sha256sum -c audit/reports.sha256` verifies the original JSON reports.
The archived workflow specifies build and scanner commands and verified binary
hashes. Reproducing a historical comparison requires the recorded source and DB
snapshots; the DB files themselves are not committed. Future advisory data can
change the findings. Zero Python findings here does not imply a vulnerability-free
OS image or establish complete application exploitability coverage.
