# Debian runtime recheck — issue #108

Status: proposed in the separate security review. The sole maintainer must
record an explicit bounded decision on the review PR before merge; no
independent reviewer is available. This review keeps the existing
**2026-11-10 00:00 UTC** expiry and hands the next check to [#123](https://github.com/flowent59/PaperWrench/issues/123).

## Exact images and comparison

| Runtime | Source and scan | Image config ID | Result |
| --- | --- | --- | --- |
| Published 0.6.3 before | [published scan 40850662320](https://github.com/flowent59/PaperWrench/actions/runs/40850662320), digest `sha256:adbf3c69f3bd2a984a4034e58838c34e97136915cbf42d74b4040ee58ed74dbd` | See [prior evidence](../2026-10-29-python315/README.md) | 323 occurrences, 71 identities, pass under prior approval |
| PR #121 candidate | `9b27d8ddb34a03966db1997651094c6212f866de`, [scan 40859615523](https://github.com/flowent59/PaperWrench/actions/runs/40859615523) | `sha256:3d064c8f323f85ea74e61ccba269ad63c782dfc269c8fc0e82df5b0dafcf2736` | 306 occurrences, 71 identities; scope review required |
| Merged main | `a1dbfbed51a6e6600eb1ed2b64263ea495e2407d`, [scan 40860804635](https://github.com/flowent59/PaperWrench/actions/runs/40860804635) | `sha256:e946ef20b802be482b65967652af1ae3d65808ce75000c44d438efccaa959da5` | 306 occurrences, 71 identities; scope review required |

The merged runtime tree is `89e9dceda661849b445a19c0d4290174585e65fcb135f63bb1b4920faa162397`.
The two current scans and the published comparison used the **same** Grype DB
checksum `sha256:a5ad3344d964e4d1472fa615b0e70f87c27cd21470fede2a8fbf6e4c6c32c907`
(built 2026-10-29 06:23 UTC) and Trivy DB checksum
`64aa2fee0759f2f4112dd5f93ae13d00071c8494d11ee34bff2cc2d76fd10da2`
(updated 2026-10-29 07:17 UTC). Raw scanner JSON, inventory, package files,
tool/DB metadata, execution logs, result and checksums are retained in the
[candidate](candidate-audit-evidence.zip) and [main](main-audit-evidence.zip)
ZIP files. The published baseline is retained in the [prior ZIP](../2026-10-29-python315/published-audit-evidence.zip).

The supported `apt-get purge` after account creation removed `mount`, `passwd`
and its `adduser` dependency. The installed Debian inventory lost exactly these
three packages and gained none. The scanners lost **17 exact occurrences**:
12 on `mount` (six advisories in both scanners), four on `passwd` (two
advisories in both scanners), and one Trivy `passwd` TEMP occurrence. No new
occurrence appeared. All 71 advisory identities remain elsewhere, so no
identity has been falsely retired. The `login.defs` file is still shipped and
its separately identified findings remain visible. `dpkg` package metadata,
native libraries and the application account are intact. APT simulation found
that essential `apt`, `util-linux`, `ncurses-bin` and `perl-base`, and `tar`
required by `dpkg`, cannot be safely removed by the same supported operation.

## Provider status and retained decisions

The [timestamped Debian tracker subset](debian-tracker-subset.json) covers the
same 71 package/advisory pairs across 20 source packages as the previous review.
Its Trixie release status, fixed version and urgency have **zero changes**.
The snapshot was retrieved on 2026-10-29 (provider `Last-Modified`:
2026-10-29 09:47:26 UTC). `apt-get update` on the exact published 0.6.3 image
listed no upgradable package. The pinned Python 3.15.2 Trixie base and the
runtime's prior PCRE2, CA and xz updates remain the supported versions used
for this comparison. No available supported Trixie security correction was
left unapplied.

Each of the [71 individual decisions](dispositions.json) records its remaining
exact scanner/package/version/path occurrences, Debian status, rationale,
owner, Linux amd64 and deployment scope, triggers, evidence and unchanged expiry.
The 17 absent occurrences were removed from their records. The evaluator was
run against the current raw main reports with the proposed runtime tree and
registry in memory: `pass`, 306 occurrences, 71 identities, zero blocked,
zero errors. The committed policy is deliberately still tied to the earlier
tree until this separate review is approved and merged.

For [zlib CVE-2026-85091](https://security-tracker.debian.org/tracker/CVE-2026-85091),
Debian still marks Trixie 1.3.1 vulnerable; a corrected 1.3.2 package is only
listed for Sid. [Upstream discussion](https://github.com/madler/zlib/issues/1310)
suggests that the older writer's `EAGAIN` handling may avoid the specific
stale-buffer path introduced later, while a Debian maintainer says older
versions may still be affected. The upstream affected range is unresolved.
Absence of `gz_vacate` is therefore insufficient to call this a false
positive; the accepted risk remains bounded and visible.

## Runtime controls and release checks

The candidate starts under UID 10001 with read-only root, private `/tmp`, all
capabilities dropped and `no-new-privileges`. Installed release, SPA and auth
checks, native TLS and encrypted credentials, and populated SQLite backup
restore with repeated migrations passed locally. CI #121's 15 jobs passed,
including live Paperless/Chromium and new assertions that the account exists
while mount/account utilities are absent. The audit inventory again confirms
Linux amd64, Debian 13, no SUID/SGID regular files, no authorized `fstab`
entries or privileged host mounts, absent Perl modules/tools and absent SQLite
symbols used by the specific code-absence decisions. These controls do not
extend the review to root, privileged, hostile filesystem or another
architecture deployments.
