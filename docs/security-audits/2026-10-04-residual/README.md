# Residual image review (2026-10-04)

Implements [#100](https://github.com/flowent59/PaperWrench/issues/100) in
[PR #106](https://github.com/flowent59/PaperWrench/pull/106), after #98 and #99.
This completes the initial disposition review, not the elimination of every
vulnerability. No image is published by this verification or PR.

## Images and audit provenance

Both images are built locally by Docker Desktop's Linux amd64 engine from the
merged application and exact pinned Node/Python bases. The baseline uses the
Dockerfile from its recorded Git commit, supplied to Docker on stdin; candidate
application/lockfile contents are identical. The candidate adds only the SUID/SGID
removal. Subsequent PR changes add documentation/evidence, not runtime code.

| Role | Source / OCI revision | Docker image/config ID |
| --- | --- | --- |
| Merged #98 + #99 | `9f3186095100062890c283f5935daa5979ac6ef9` | `sha256:1ec655893c77b0158a3e1a27449c4531269e486a39b9105a5b30a14acdd754d3` |
| Privilege-bit removal | `ad0286c48b7afa5b6522548797b9e00f981aeb94` | `sha256:923d71ac9a5b4544614995d74a79a8ff7ebf2f9e05daea6bf4ad1a0c3b61196c` |

These config IDs identify the images actually loaded/scanned, not registry pull
references. Neither is the moving published `latest` tag. Inspect JSON, build
logs, exact source revisions, build-input hashes, package inventories, runtime
paths and verification results are in [audit-evidence.zip](audit-evidence.zip).

The current official `python:3.11-slim-trixie` index still resolves to the pinned
`bab1b7ef…` digest at this review. CPython is 3.11.17, glibc 2.41, SQLite 3.46.1,
OS OpenSSL 3.5.7 and cryptography's bundled OpenSSL 4.0.3. The Debian and installed
Python inventories are identical before/after. An isolated root container runs
`apt-get update` followed by `apt-get -s upgrade`: **zero available upgrades**.
No retained Debian finding has a released Trixie fixed version in the captured
provider snapshot. Upstream/Sid corrections are recorded separately; they are
not treated as installable Trixie updates.

Scanners run as verified Linux binaries in a separate container, not installed
into either audited image. The initial Windows Grype attempt failed on colons in
layer-cache filenames; it is a technical failure, not a scan with zero findings.
Both successful scans use immutable image IDs and one frozen cache per scanner:

- Grype **0.120.0** / Syft **1.54.0**, DB **v6.1.10**, built
  `2026-10-04T08:11:47Z`. Downloaded archive SHA-256:
  `2bd87418b8877e75d07351ef8daa9636ef98d69e09e0f69a15ae9c191e895d39`.
- Trivy **0.75.0**, DB schema **2**, updated
  `2026-10-04T08:52:32.606987838Z`, downloaded
  `2026-10-04T13:24:17.3351565Z`. DB updates are disabled for both comparison
  scans; the reports and cache metadata retain the dates.

The archived `started-at.txt`/`completed-at.txt` retain UTC execution times.
Linux binary archive hashes are verified against the previously captured release
assets. No advisory is ignored, no SBOM/package metadata is removed and no scanner
severity is filtered. Database files are not committed; the recorded DB archive
identifier/hash and metadata distinguish this historical comparison from future
scans with different advisory data.

## Raw results and decisions

| Scanner | Entries before → after | Critical | High before → after |
| --- | ---: | ---: | ---: |
| Grype | 167 → 167 | 0 | 55 → 55 |
| Trivy | 166 → 166 | 0 | 44 → 44 |

Grype retains 160 Debian and seven CPython binary entries; Trivy retains 166
Debian entries. Installed Python distributions have no reported finding in either
image scan. The separate installed-distribution pip-audit proof remains in the
[#99 audit](../2026-10-04-dependencies/README.md); it is not a new pip-audit run
and does not establish absence of CPython or OS vulnerabilities.

[findings.csv](findings.csv) maps **333 scanner/package/advisory occurrences** to
**80 advisory identities** (75 CVEs and five Debian TEMP identifiers), including
**15 identities rated High by at least one scanner**. Do not add scanner totals
as distinct CVEs. Explicit primary-source GHSA aliases are attached to their
identity; related-but-not-identical CVEs and unconfirmed TEMP links stay distinct.
Baseline/candidate package/advisory sets, versions and severities are identical.

Every identity has an explicit assessment, exact package/version occurrences,
scanner locations, installed component paths/full file-manifest reference,
primary sources, owner, priority, review triggers and expiry in
[dispositions.json](dispositions.json). The readable
[decision table](decisions.md) summarizes them:

| Disposition | Advisory identities | Meaning |
| --- | ---: | --- |
| Code not shipped | 14 | Positive file/module/symbol inspection for the described component |
| Architecture not applicable | 1 | Power8 routine does not apply to this Linux amd64 image |
| Mitigated | 8 | Vulnerable code remains; the reviewed privilege/deployment conditions constrain it |
| Accepted residual risk | 57 | Code or uncertainty remains, with bounded review and exact scope |

These are proposed project decisions for review/approval through PR #106. Merging
the PR approves this bounded register; no scanner suppression is configured by
this work. Owner: **PaperWrench maintainer (@flowent59)**. Every decision expires
on **2026-10-18 at 00:00 UTC** (02:00 Paris), or must be reconsidered sooner on
its listed change triggers. An open follow-up does not automatically renew it.
High/Unknown identities are P1; the remaining identities are P2. This prioritizes
urgent review without dropping the other severities.

## Important evidence and limits

- **Perl:** `Pod::Text` and `Archive::Tar` fail to import and their affected module
  files are absent. `File::Temp` **is importable** and the regex engine remains;
  those risks are retained. `ivsize=8` only confirms this interpreter's integer
  width: it does not disprove a regex cache overflow using a signed 32-bit count.
  Application source has no Perl execution, but that alone is not proof of safety
  for arbitrary operator scripts.
- **mount/util-linux:** vulnerable tools remain. The baseline contains **11**
  SUID/SGID regular files; the candidate contains **zero** under `/usr`, including
  account tools, mount/umount and `unix_chkpwd`. `/bin` and `/sbin` resolve into
  `/usr` on this image. Its fstab contains no authorized mount entry. The verified
  deployment uses UID 10001, a read-only root, private `/tmp` tmpfs, dropped
  capabilities and `no-new-privileges`. Root `nsenter`, privileged containers,
  hostile host mounts or altered fstab/file modes invalidate this assessment.
  Package metadata and executable files remain available to scanners.
- **glibc:** DNS debug printers are different from the ordinary resolver path;
  `strfmon`, `wordexp`, native POSIX regex and iconv need their own consumers.
  No such application calls were found, but the native transitive call graph is
  not exhaustive. DNS resolution is used; resolver configuration and LOCALDOMAIN
  must be trusted for CVE-2026-8674. nscd is absent. CVE-2026-97399 describes a
  Power8 optimization; this decision grants no exception to another architecture.
- **SQLite:** the actual library enables Session support and exports concat/
  changegroup functions. It does **not** export `sqlite3changeset_apply_v3` or
  `sqlite3_zipfile_init`, and the SQL zipfile probe reports no such function.
  Application SQL uses bound ORM values/fixed migration statements, without a
  remote arbitrary-SQL, changeset or extension-loading interface. A hostile
  database/backup or a future such interface changes the risk; keep `/data` and
  restoration inputs trusted/private.
- **CPython:** all seven remaining binary findings are assessed against upstream
  CNA records and branch backports. Newer-branch fixed versions do not make
  3.11.17 fixed. Fernet authenticates decoded bytes and owner/instance binding;
  Base64 spelling tolerance does not currently authorize a different credential.
  No app IMAP, POP or `Morsel.js_output` path exists. TemporaryDirectory is used
  by verification scripts, not the app; the cleanup race remains a residual risk
  for shared hostile filesystem writers. The early 3.12 cleanup backport was
  reverted; 3.13/3.14 backports were still open at review, so neither scanner nor
  CNA fixed ranges alone justify a blind migration. [#107](https://github.com/flowent59/PaperWrench/issues/107)
  tracks a verified stable branch migration/correction. The older
  CVE-2026-82049 report is no longer in this audit; the
  [3.11.17 release notes](https://www.python.org/downloads/release/python-31117/)
  identify its fix, without downgrading to 3.10.
- **zlib:** runtime is 1.3.1. Debian source/patch archives match their DSC SHA-256
  hashes and contain no `gz_vacate`. However upstream discussion includes
  ambiguity about older-version PoC behavior. CVE-2026-85091 is retained as
  accepted uncertainty, **not** declared a definitive scanner false positive.
  The app has no non-blocking gzip writer/gzprintf pipeline.

The application import snapshot does not load libstdc++; it does load several
other native libraries. This is evidence for that execution, not proof that a
library can never load later. No lack-of-use inference is generalized to root
debugging, user-supplied binaries/scripts or a compromised host.

## Deferred corrections and frontend scope

[#108](https://github.com/flowent59/PaperWrench/issues/108) tracks Debian/provider
corrections and every non-CPython decision before expiry, including a supported
evaluation of unused tools. It has P1 priority and concrete rescan/verification/
renewal criteria. [#107](https://github.com/flowent59/PaperWrench/issues/107)
tracks CPython release/backport verification and compatibility/migration tests.
Neither means that the corresponding risks have been fixed.

A fresh npm full-tree audit still reports **five High package entries** for one
advisory, GHSA-vfj7-8cjw-p6xm / CVE-2026-93687, with no patched braces version.
The vulnerable build chain remains in [#104](https://github.com/flowent59/PaperWrench/issues/104).
`node_modules` is not copied by the multi-stage Dockerfile; runtime filesystem
inspection also confirms it absent. Builder/CI risk remains visible and is not
covered by runtime exceptions. Node builder OS/packages, Paperless-ngx's separate
image and the host OS are outside this runtime inventory and must not inherit its
exceptions.

## Policy and verification

The [image scan policy](../../security-image-policy.md) defines thresholds,
unfixed findings, exact exceptions, expiry, technical failure/freshness behavior,
PR/main/published-image scopes and retention. [#101](https://github.com/flowent59/PaperWrench/issues/101)
implements and tests recurring blocking enforcement; this PR does not claim that
the existing CI already enforces the new vulnerability policy.

The exact candidate passes installed-release/SPA checks, native verified TLS,
Fernet/Argon2, populated SQLite backup restore and repeated migrations under the
stated container restrictions. Build `pip check` remains mandatory. Existing CI
checks remain intact; the Docker job additionally fails if an inherited SUID/SGID
regular file reappears. Full PR CI covers mocked/live Paperless, the compiled
Chromium/accessibility journey, migrations, multi-user security and frontend/
backend quality checks. See [PR checks](https://github.com/flowent59/PaperWrench/pull/106/checks).

## Reproduction and integrity

[SHA256SUMS](SHA256SUMS) covers the committed raw archive, CSV and disposition
register. The archive contains the exact build/scan/probe/smoke scripts and input
snapshots, source/provider/CNA records and report hashes. The source archives were
checked against HTTPS-delivered DSC hashes; this is not a claim that their PGP
signatures were independently verified.

On Windows with Docker Desktop, unpack the ZIP into a temporary directory, set
the repository path in the archived Python helpers and run build → DB import →
Linux scan → probe → smoke. Fresh runs must obtain current DBs; reproducing these
historical counts requires the recorded snapshot. No production volume, key or
database is used. An integrity/coverage verifier can run offline:

```sh
python -m zipfile -e audit-evidence.zip extracted
python extracted/evidence/verify-dispositions.py . extracted
```

Always preserve fresh raw reports when data changes; a lower count alone does not
prove lower application risk, and this review is not a penetration test.
