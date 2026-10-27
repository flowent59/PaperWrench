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

This review accompanies a source change that pins `certifi 2026.10.23` and requests the supported Debian security upgrades for `ca-certificates` and `liblzma5` during the image build. A local Linux amd64 Docker build completed on 2026-10-27. A non-root, network-disabled, read-only container with dropped capabilities reported UID 10001, `certifi 2026.10.23`, `ca-certificates 20250419+deb13u1`, `liblzma5 5.8.1-1+deb13u2` and `libpcre2-8-0 10.46-1~deb13u3`. This is package-installation evidence, not a fresh vulnerability verdict. The candidate still needs an exact-source CI image scan with fresh databases, full inventory, native probes and functional gates. The old published image remains affected until a corrected release is built and published.

A separate full frontend dependency-tree `npm audit` initially found seven
entries (three High and four Moderate). Compatible lockfile updates, including
Vite 6.4.5, Zod 4.6.7 and Vitest 5.0.5, reduced that report to zero known
findings. This is a build/development dependency audit and does not replace the
runtime image scan; the updated lockfile must be verified by `npm ci` and the
frontend test/build gates.

The existing policy must continue to report an error while its reviewed runtime scope and decisions are expired. A new dated decision register needs individual reassessments, exact occurrences, owner, expiry, evidence, and review through a separate policy PR. No exception is extended or scanner finding suppressed by this report.
