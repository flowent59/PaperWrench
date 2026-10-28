# Proposed Linux amd64 runtime review, 2026-10-27

**Status: proposed, not approved.** This is a separate review of the Python
3.14.10 runtime merged in [PR #113](https://github.com/flowent59/PaperWrench/pull/113)
as `5bb29cfa9bee7d418f3ad1ec83cd295352ab85d2`.
It does not approve itself. The approved base policy remains expired until this
review has been independently checked and merged. The published 0.6.1 image is
unchanged.

## Exact scope and proof

- Candidate CI: [run 40648884080](https://github.com/flowent59/PaperWrench/actions/runs/40648884080), whose Docker image job completed with fresh scanner databases before the run was superseded by this evidence update; the image audit correctly returned `technical-error` against the expired approved base. Its 325 occurrences and 73 identities exactly match the preceding #113 scan.
- Image/config ID: `sha256:8c347d82905e7e09161fe17380ff34891822f0d948f1f6d1be0145db70effc8a`; OCI/source revision: `423502506cbfa609d347e8cb2914aab57483f11b` (PR merge checkout); runtime tree: `d91211d9044f4cc2dae7813056ef41f5f18c7f3107497c9da201d8460a17dc52`.
- Scope: Debian 13, Linux amd64, UID 10001, read-only root, all capabilities dropped, `no-new-privileges`, private `/tmp` tmpfs, trusted `/data` and backup inputs, no privileged host mounts or authorized fstab entry. The probe and container configuration are inside [audit-evidence.zip](audit-evidence.zip) (`sha256:2a0df4ec7dc552c5f4f8dd6db26b3356fc29d2a4fe8fb12678e0584565ce121c`).
- Scanner/DB versions: Grype 0.120.0 (DB built 2026-10-28 06:23:30 UTC); Trivy 0.75.0 (DB updated 2026-10-28 01:21:08 UTC). Unfiltered raw reports, inventory, metadata and checksums are in the archive.
- Probes compared with the prior 3.11 candidate changed only Python version/stdlib/loaded Python libraries and Debian `tzdata` (`2026c` to `2026e`). The 71 non-Python advisory identities retained exact scanner/package/version/path matches. `CVE-2026-102473` severity decreased from Medium to Low at identity level; no severity increased.

The reports contain **325 scanner occurrences and 73 advisory identities**: 99
High, 108 Medium, 71 Low, 46 Negligible, and one Unknown occurrence. The
[individual table](decisions.md) and [machine-readable register](dispositions.json)
keep all findings visible. Proposed dispositions are 50 accepted risks, 14
components whose affected code is not shipped, eight mitigations and one
architecture mismatch. Every record has exact occurrences, owner, sources,
assessment, follow-up, and proposed expiry **2026-11-10 00:00 UTC**. No
scanner ignore rule or package/severity-wide exclusion is added.

## Current correction check

An `apt-get update` and `apt list --upgradable` on the candidate returned no
available Debian package upgrade. The official Debian tracker snapshot of
2026-10-28 06:06:21 GMT was checked for all 71 package/advisory pairs in the
prior register: all remain open for Trixie. Seventeen entries gained a `no-dsa`,
`next_point_update`, or urgency qualifier, captured exactly in
[debian-tracker-subset.json](debian-tracker-subset.json). None records a
released correction for the installed package. These Debian classifications
are context, not automatic risk acceptance.

The two new exact Python occurrences are `CVE-2025-15367` (`poplib` command
injection) and `CVE-2026-12345` (`TemporaryDirectory` cleanup race) on Python
3.14.10. [CPython #143923](https://github.com/python/cpython/issues/143923)
and [CPython #157579](https://github.com/python/cpython/issues/157579) describe
the affected code. The installed `poplib.POP3._putcmd` still lacks command
control-character rejection. Production PaperWrench has no POP3 or
`TemporaryDirectory` call; the latter is used in smoke and backup verification
with private temporary directories. A hostile writer in the same directory
remains a condition to revisit. The reports point to fixes in stable
[Python 3.15](https://www.python.org/downloads/release/python-3152/), but the
current exact `httptools 0.8.0` and `pydantic-core 2.46.5` locks have no
Python 3.15 Linux binary wheel. Moving to 3.15 requires a coordinated
dependency refresh and full compatibility testing under [#107](https://github.com/flowent59/PaperWrench/issues/107).
The two findings are retained as short-lived accepted risks, not called fixed.

## Review and approval

Review the 73 records, especially the 17 changed Debian qualifications and two
new Python occurrences, against the raw evidence and sources. The previous
[2026-10-04 register](../2026-10-04-residual/dispositions.json) provides the
original code-path rationale; this review records the current exact scope and
reassessment in every record. A maintainer must approve the bounded register
in this separate PR. The CI evaluator reads
the approved base branch, so changing this proposed register in its own PR
cannot turn its scan green. After approval, rebuild/publish the release image,
scan its exact registry digest, and review any resulting identity or scope
change before treating that image as covered.
