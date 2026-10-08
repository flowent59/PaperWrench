# Image vulnerability scan policy

Defined by [#100](https://github.com/flowent59/PaperWrench/issues/100), approved
through review/merge of [PR #106](https://github.com/flowent59/PaperWrench/pull/106).
[#101](https://github.com/flowent59/PaperWrench/issues/101) implements and tests
the automation in [PR #109](https://github.com/flowent59/PaperWrench/pull/109).
The [execution guide](image-security-ci.md) describes the audit verdict, daily
published-image scan, retained evidence and local commands. Automation becomes
active on main when that PR is merged. Existing functional Docker/release checks
remain mandatory.

The image audit is advisory for deployment: a failed audit remains a failed
security assessment with its findings and evidence visible, but does not fail the
CI Docker job or stop a release. Functional build, runtime and application tests
remain deployment checks. Expiry of a risk decision requires review; it does not
disable an already running image or prevent publication by itself.

## Scope and severity decisions

Scan the actual final runtime image by immutable ID/config digest, recording its
Git/OCI revision, architecture, base digests, installed inventory, scanner versions
and DB metadata. Do not compare a moving tag with an unrelated build. Grype and
Trivy raw reports remain separate; summaries group only verified advisory aliases
and retain each scanner/package/version/path occurrence. Use the highest reported
severity when scanners disagree. Unknown/TEMP identifiers remain visible.

- **Critical and High:** block unless an exact current reviewed exception matches.
  Lack of a vendor fix does not exempt a finding. Fix available vulnerabilities
  rather than renewing an exception to avoid an available supported correction.
- **Unknown or unrecognized severity:** block for manual classification, unless
  that exact identifier/occurrence already has a current explicit decision.
- **Medium, Low and Negligible:** preserve and prioritize review; require a
  documented disposition within 30 days of first discovery. Overdue unreviewed
  findings block. Explicit risk/mitigation/absence decisions still expire.
  New lower-severity identities require an approved initial `first_seen` UTC date;
  missing classification blocks instead of resetting their deadline each scan.
- An expired, malformed or unverifiable exception is a **policy error that
  fails the audit**, including an expired entry for a lower severity. Never silently extend
  dates or fall back to a package/severity exclusion.

The current review covers Linux amd64 runtime only. Frontend full-tree npm audits
must have separate findings/decisions; [#104](https://github.com/flowent59/PaperWrench/issues/104)
tracks the five unpatched braces build entries. “Not in the runtime” is not a
blanket acceptance of builder/CI vulnerabilities. Published architectures and
new dependency scopes need separate evidence; do not reuse amd64 exceptions on
ppc64le or another image.

## Exact exceptions and ownership

The initial [register](security-audits/2026-10-04-residual/dispositions.json) has
80 explicit advisory decisions. The CI evaluator reads the approved base registry;
it is never a Grype ignore file or Trivy ignore configuration. Every record includes aliases,
exact scanner/package/version/type/path occurrences, installed file evidence,
primary sources, disposition/rationale, priority, owner, expiry and follow-up.

An implementation must match **all** of these, not just a CVE substring or package:

1. A verified advisory identity/alias, exact package identity/type, installed
   version and relevant scanner/component paths.
2. Reviewed OS/distribution/architecture and deployment controls. Changed path,
   package version, architecture, native/app input behavior or deployment scope
   invalidates the decision; an image digest/code change requires revalidation.
3. Severity at or below the recorded maximum; a raised severity needs review.
4. A future UTC expiry, named responsible maintainer, evidence/rationale and an
   open/visible follow-up when correction or maintenance is deferred.
5. Approval through code review/merge. New exceptions proposed in a PR cannot
   automatically approve themselves by making a scanner exit zero.

Code absence, incorrect version/architecture matching, mitigation and accepted
risk are distinct dispositions. Debian `no-dsa`, `ignored`, scanner `wont-fix`, a
vendor dispute or no known app use are evidence, not automatic risk acceptance.
Keep exceptions and their matching findings in summaries, not only an exit code.
Remove exceptions when a supported correction is applied; never delete package
metadata/SBOMs to suppress a result.

Current owner is the PaperWrench maintainer (@flowent59); expiry is
**2026-10-18 00:00 UTC** for every initial record. Reassess sooner on a released
fix, new exploit/affected-range evidence or its specific input/deployment change
trigger. [#107](https://github.com/flowent59/PaperWrench/issues/107) and
[#108](https://github.com/flowent59/PaperWrench/issues/108) track CPython and Debian
maintenance; an issue remaining open does not renew its exceptions.

## Database freshness and technical failures

Acquire current DB snapshots once per scan/comparison; use that same snapshot for
both before/after images. Verify supported DB schema, integrity and reported
UTC build/update time. A DB timestamp more than **24 hours** old, more than five
minutes in the future, missing metadata or an unsupported schema is a technical
failure. Do not silently accept an older cached DB when fetching fails. Preserve
the fetch/validation error and fail the security job separately from findings.

Scanner crash, timeout, nonzero technical exit, invalid/truncated JSON, missing
inventory, identity mismatch or artifact failure must not become “zero findings.”
Validate successful execution and report structure before evaluating findings.
Document verified tool releases/checksums; changing scanner/schema versions needs
a compatibility review. Historical comparisons can use a deliberately frozen DB
only as labeled evidence, never as a current passing security gate.

## CI, periodic scans and retained evidence

#101 scans PRs and main builds and performs a **daily** scan of the published
image resolved to its immutable registry digest. Record both registry digest and
image/config identity, matching OCI revision and architecture. A scan does not
publish or deploy an image. Fork PRs run with read-only permissions and no
publication secrets; security tooling receives no application credential.

Preserve raw JSON, optional SARIF, normalized summary (including exceptions),
inventories, input/image/tool/DB metadata and logs **even on findings or technical
failure**. CI artifacts have **90-day retention**; release/baseline decisions and
their raw proof should also be kept in versioned audit evidence so they survive
artifact expiry. Do not include user tokens, encryption keys or private databases.

Before closing #101, verify a successful scan, an unexcepted threshold finding, an
unfixed High, changed package/path/severity/scope, technical failure, stale/missing
DB, malformed report and expired/unapproved exception. Retain the functional
Docker, native/SQLite, release and fork-permission checks. Report availability can
land first; the issue is complete only after blocking behavior is demonstrated.

Local commands, current scanner installation hashes and comparison commands are
in the [residual audit evidence](security-audits/2026-10-04-residual/README.md).
Keep scan tools outside the audited image and compare inventory before/after any
runtime correction. Updating a base/tool digest or exception is a reviewable code
change with fresh proof, not an automatic renewal.
