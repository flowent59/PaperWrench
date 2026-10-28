# Frontend dependency audit, 2026-10-28

The complete `frontend/package-lock.json` on the stacked security branch was
audited with `npm audit --json --audit-level=low` using npm 11.16.0. The audit
returned exit code 0 and **zero** info, low, moderate, high, or critical
vulnerabilities across 412 dependencies (17 production, 396 development,
53 optional, and 8 peer; npm's categories overlap). The unfiltered result is
saved as [2026-10-28-frontend-audit.json](2026-10-28-frontend-audit.json).

Lockfile SHA-256: `dfa8ad92d0984605e5b5275b31762597361766a031cc44fc91cb0e78a1b5087f`.
The resolved lockfile has `@vitest/mocker@5.0.5` and `source-map-js@1.2.2`.
The three Dependabot alerts still open on `main` for those packages point to
versions below these locked versions; they will be reassessed after the runtime
fix PR is merged.

The five previously reported npm entries under
[GHSA-vfj7-8cjw-p6xm](https://github.com/advisories/GHSA-vfj7-8cjw-p6xm)
were one advisory propagated through the `braces` dependency chain. GitHub
withdrew that advisory on **2026-10-23**, stating it was not a practically
exploitable vulnerability. `braces@3.0.3` remains in the build dependency
tree, but the current npm audit reports no advisory for it. This is an
advisory withdrawal, not a claim that a patched `braces` release exists.
Consequently, the Tailwind migration proposed in [#104](https://github.com/flowent59/PaperWrench/issues/104)
is no longer a security correction for that finding.

The frontend audit is separate from the Linux amd64 runtime image review.
Future npm advisories still require their own investigation and disposition.
