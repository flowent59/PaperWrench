# Proposed 0.6.2 runtime scope revalidation, 2026-10-28

The [0.6.2 release PR #115](https://github.com/flowent59/PaperWrench/pull/115)
passed all 15 functional CI jobs in [run 40708076834](https://github.com/flowent59/PaperWrench/actions/runs/40708076834).
The Docker job built and scanned the Linux amd64 release candidate from PR head
`8144bd0f6afccb8573b9f625b7725437701cbe21` (CI merge checkout source
`90e59970ef618a96e976bb1aafb7816bed9c3fae`). Its image/config ID is
`sha256:5eb1f07d3ebade3b856946d1d1beebfa655373001383e1037419e5815cae0302`.
The runtime tree is `b1cb7bee4822beda43c2236b8192caede2dd9a161b2d1f4412b574ccd8971fe1`.

The unfiltered Grype and Trivy reports have **325 occurrences / 73 identities**,
with zero added or removed exact scanner/package/version/path occurrences when
compared with the [Python 3.14.10 review](../2026-10-27-python314/README.md).
Grype 0.120.0 used a DB built 2026-10-28 06:23:30 UTC; Trivy 0.75.0 used a DB
updated 2026-10-28 13:01:10 UTC. The raw reports, installed inventory, image
and container metadata, probe, DB metadata, tool versions and checksums are in
[audit-evidence.zip](audit-evidence.zip)
(`sha256:23b5d975faa0220b50ddd1ef4c1d858802302fba9f06c02d4e1a881a3591a97c`).

The Git diff of runtime paths between merged #113 and release PR #115 changes
only the application version from `0.6.1` to `0.6.2` in backend and frontend
metadata plus the default image reference in `docker-compose.yml`. Dockerfile,
dependency pins and application behavior are unchanged. The candidate retained
the same third-party installed package versions and runtime/deployment probes;
only the `paperwrench` package version changed from 0.6.1 to 0.6.2. The new tree
is included as a bounded additional scope in `.security/reviewed-runtime.json`,
with the same **2026-11-10 00:00 UTC** expiry as the individual decisions.

The CI image audit reports `technical-error` against the previously approved
expired policy until [PR #114](https://github.com/flowent59/PaperWrench/pull/114)
is merged. This candidate scope is not evidence that an image has already been
published. After publication, scan the immutable registry digest and compare
its exact runtime tree, inventory and findings before calling the release
covered by the decision register.
