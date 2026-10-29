# Python 3.15.2 runtime review — issue #107

This review proposes the exact Linux amd64 runtime scope for PR #119. It does not
approve itself. The sole maintainer must record the bounded decision on that PR
before merge; no independent reviewer is available. The 2026-11-10 00:00 UTC
expiry of the previous decisions is unchanged.

## Exact evidence

| Image | Source/CI | Config ID | Runtime tree |
| --- | --- | --- | --- |
| Main, PaperWrench 0.6.2 | `5bc74cf6855b10b4e02f4acb697f372c6e12d02b`, [run 40848406610](https://github.com/flowent59/PaperWrench/actions/runs/40848406610) | `sha256:4ceea4dea1b1ced00fdb950e6b544e886ea1dcb70ec6a852b455321ab1a4f80e` | `242e35807957d2e0f9db2735bf0db12b716136fd88d97fd3bdf8ac2ddc1a7214` |
| 0.6.3 candidate, PR #118 | `9ea6fe75adbc44ef67f8a8746ffdbf7e3c07f041` (CI merge revision `b4748c97608418a6077785a5e50688a8a935e194`), [run 40848454079](https://github.com/flowent59/PaperWrench/actions/runs/40848454079) | `sha256:1d5ff7886474a460a86162f47c48f9aa0a8713d811577c63a7f89431b46cb0b7` | `2fd41831bfb99b071f5f085d7b2b7c99852dfb745832a5dd125351e10d043ac5` |

The ZIP files preserve each run's raw Grype/Trivy JSON, inventory, image and
container inspection, database/tool metadata, logs, result, summary and checksums.
Both DB snapshots were current: Grype built `2026-10-29T06:23:34Z`; Trivy updated
`2026-10-29T07:17:11.934066875Z`. Both scanners completed successfully. The
result exit code is 2 because the approved base policy still covers Python 3.14;
this is the intended review boundary, not a passing audit verdict.

## Finding and runtime delta

Both Python 3.15.2 images have **323 exact occurrences / 71 identities**. The
[Python 3.14 review](../2026-10-27-python314/README.md) had 325 / 73. The absent
identities are `CVE-2025-15367` (`poplib`) and `CVE-2026-12345`
(`TemporaryDirectory`). Python 3.15.2's installed `poplib.POP3._putcmd` rejects
control characters; its `TemporaryDirectory._rmtree` uses directory descriptors
for the permission-retry path. These correspond to the [CPython poplib issue](https://github.com/python/cpython/issues/143923)
and [tempfile issue](https://github.com/python/cpython/issues/157579). The
corrected runtime is [Python 3.15.2](https://www.python.org/downloads/release/python-3152/).

The 323 retained occurrences match the previous register on scanner, advisory,
package, installed version, package type and path. No severity or reported fix
changed. The Debian inventory adds `libmpdec4:amd64` 4.0.1-5, with no finding in
either report. The remaining Debian package versions are unchanged. Third-party
Python changes are MarkupSafe 3.0.4, httptools 0.9.0, pydantic 2.14.1,
pydantic-core 2.50.1, uvloop 0.23.0 and websockets 17.3; unused PyYAML is gone.
The candidate changes only PaperWrench 0.6.2 to 0.6.3 and Compose version
references relative to main. Native TLS/Fernet/Argon2, an encrypted local
credential, SQLite backup restore/repeated migration, installed SPA, non-root
read-only runtime and `pip check` passed; the CI suites exercise mocked and live
Paperless and Chromium.

The new register carries forward 71 individual decisions (15 High, 24 Medium,
32 Low; 48 accepted risk, 14 code absent, 8 mitigated, 1 architecture not
applicable), each with its existing owner, evidence, exact occurrences, follow-up
and expiry. The two Python records are removed. Debian follow-up remains #108.
Any new scanner evidence, package/path/severity change or released fix requires
fresh review before the unchanged expiry.
