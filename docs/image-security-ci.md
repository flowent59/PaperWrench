# Runtime image security audits

[Policy](security-image-policy.md) · [initial reviewed decisions](security-audits/2026-10-04-residual/dispositions.json)

## Execution and evidence

`CI / Docker image` audits the **image ID returned by the existing build step**,
after the startup, installed-release, native TLS, SQLite restore and hardening
checks. `VCS_REF` records the exact checkout revision, including GitHub's PR merge
commit. The audit still executes when a functional check fails after a successful
build. It never pushes an image.

`Published image security` runs daily at **05:23 UTC** and on manual dispatch.
It resolves `ghcr.io/flowent59/paperwrench:latest` once, pulls the immutable registry
digest for Linux amd64, then scans its local image ID. Both identities and the OCI
revision are retained. The published revision's Git tree is fetched if necessary;
it is never treated as the current main revision just because a tag says latest.

Both workflows use `contents: read`, without registry login, publication tokens,
application credentials or `pull_request_target`. Fork PRs run the Docker build
and scans with the same read-only permission. The public published image needs
no registry secret. The disposable inventory container has no network, a fresh
anonymous `/data` volume, read-only root, private `/tmp`, all capabilities dropped
and `no-new-privileges`; it is removed with its volume after the probe.

The composite action always uploads `image-security-audit` (CI) or
`published-image-security-<run-id>` (scheduled/manual), retained **90 days**:

- separate, unfiltered `grype.json` and `trivy.json`, plus stderr logs;
- `metadata.json`: exact image inspection, source/approval commits, runtime tree
  hash, pinned base references, tool releases/checksums, scanner exits and DB metadata;
- scanner version JSON, DB manifest/download log and `databases.json`;
- `inventory.json`: Debian packages, Python distributions, package file lists,
  native versions/symbols, installed tooling and relevant scope probes;
- `container.json`: the inspected deployment controls used for the probe;
- copies of the **approved** policy/decisions, `result.json`, `summary.md`, `SHA256SUMS`.

Files that could not be produced are absent after an early technical failure;
the nonzero result and diagnostic summary remain available. The summary is also
attached to the Actions run. Artifact upload failure is fatal. JSON is the source
of truth; SARIF is not needed for these package findings. No severity, package,
fix-status, ignore/VEX or `.grype.yaml` filter is applied. Scanner commands run in
a clean directory with explicit empty configuration and Trivy ignore file.

`Security / image policy regressions` retains a JUnit report for the blocking
tests for 90 days. Historical scans in tests use the deliberately frozen #100
evidence/time; they cannot serve as a fresh current security gate.

## Tools and database acquisition

Linux amd64 archives are downloaded from their upstream GitHub releases and
verified before extracting only the regular scanner binary:

| Tool | Release asset | SHA-256 |
| --- | --- | --- |
| [Grype 0.120.0](https://github.com/anchore/grype/releases/tag/v0.120.0) | `grype_0.120.0_linux_amd64.tar.gz` | `a5a1218dce63acdac152a6b3b5bb366e7267e36f4069848cf455543b3fa5700e` |
| [Trivy 0.75.0](https://github.com/aquasecurity/trivy/releases/tag/v0.75.0) | `trivy_0.75.0_Linux-64bit.tar.gz` | `c6e65abddb348e25f10549df887045629cf28cc72453cd1c63acb717316b3f3f` |

Every invocation creates empty scanner caches. Grype's active v6 manifest is
fetched from `https://grype.anchore.io/databases/v6/latest.json`; its archive is
verified against the manifest SHA-256, then imported. Trivy downloads its v2 DB
through its OCI client, which verifies content digests. Its downloaded metadata
must have supported schema and a fresh `UpdatedAt`. Grype's scanned DB must also
be valid and match the acquired manifest's build time. Both DB timestamps must
be at most 24 hours old and at most five minutes ahead. Auto-update is disabled
for the scans after acquisition; there is no fallback cache.

To update tools, change the release version/asset/checksum in `scripts/scan_image.py`
and the compatible versions/schema checks in `scripts/image_security.py`, verify
upstream releases/checksums and report compatibility, update these docs/tests,
and obtain fresh scan evidence in a PR. DBs refresh every run; their content
digests and dates, rather than a moving release tag, identify each snapshot.

## Exit codes and review boundaries

| Exit | Meaning |
| --- | --- |
| `0` | Verified scan; every occurrence has an applicable decision or current review deadline |
| `1` | Unexcepted threshold finding, overdue review or newly reported correction |
| `2` | Technical failure or invalid/expired/unverifiable policy |

Critical/High/Unknown findings block without an exact current approved decision,
including findings with no fix. The highest reported severity is used across
verified aliases. Every scanner/package/version/type/path occurrence must match.
All initial decisions expire on **2026-10-18 at 00:00 UTC**, including Low entries;
an expired entry anywhere in the register is a policy error.

For new Medium/Low/Negligible identities, a maintainer must first record the actual
earliest discovery time in the approved policy's `first_seen` map. Until that
initial classification is approved the gate blocks, so repeated scans cannot
reset the 30-day deadline. A first-seen date grants at most 30 days for a documented
disposition; it never exempts High/Critical/Unknown or an expired decision. Use
retained raw evidence to establish that date; never move it forward on rescan.

On a PR, policy and registry come from `pull_request.base.sha`, **not its modified
files**. On main/daily runs, they come from the checked-out main commit. The first
PR bootstraps only from the immutable reviewed #100 commit
`1cfb40cbf6187acc499f491fa24b7a56ae35efd6`, requiring the same runtime tree and
registry checksum. `.security/reviewed-runtime.json` binds the approved registry
checksum to Git blob identities for Dockerfile, `.dockerignore`, Compose,
backend and frontend. Changes to those inputs invalidate the reviewed runtime;
runtime absence, symbols, installed tools and deployment controls are also probed.
Digest changes from CI labels alone do not renew or broaden exceptions.

To correct an image or reassess #107/#108, preserve raw findings, verify new
inventory/app/native scope, update exact occurrences/evidence/dispositions and
remove obsolete decisions. Update the registry checksum and runtime tree hash
with fresh proof and explicit review. Do not extend expiry automatically. A PR
proposing new decisions can remain blocked against its old approved base: its
candidate policy is only a review proposal. Merge approval is required before
those decisions can pass an authoritative main/daily gate. Never bypass this
boundary by supplying the candidate commit as the approved PR ref.

## Local commands (Linux amd64 / WSL with Docker)

Run from a full Git checkout with Python 3.11+, Docker/Buildx and network access.
The scanner installation occurs outside the runtime image. Choose an already
reviewed immutable main/base commit for `APPROVED_REF`.

```bash
git fetch origin main
APPROVED_REF=$(git rev-parse origin/main)
docker build --build-arg VCS_REF="$(git rev-parse HEAD)" -t paperwrench:audit .
IMAGE_ID=$(docker image inspect paperwrench:audit --format '{{.Id}}')
python scripts/scan_image.py --image "$IMAGE_ID" --approved-ref "$APPROVED_REF" --output image-audit

# Daily workflow equivalent: immutable registry digest is resolved internally.
python scripts/scan_image.py --published --image ghcr.io/flowent59/paperwrench:latest \
  --approved-ref "$APPROVED_REF" --output published-audit

# Gate regression evidence; only test fixtures use a frozen historical timestamp.
python -m pytest tests/backend/unit/test_image_security.py --noconftest \
  --junitxml=image-policy-tests.xml

# Review helpers: Git objects avoid Windows checkout newline differences.
PYTHONPATH=scripts python -c 'from scan_image import runtime_tree; import subprocess; print(runtime_tree(subprocess.check_output(["git", "rev-parse", "HEAD"]).decode().strip()))'
git show HEAD:docs/security-audits/2026-10-04-residual/dispositions.json | sha256sum
```

Use separate output directories for comparisons and check package inventories
before attributing a vulnerability delta to a correction. Each normal invocation
acquires fresh DBs, so compare recorded DB dates/digests as well. For a controlled
same-snapshot historical comparison, retain/freeze both reports explicitly as
evidence, never label their replay a passing current CI scan.

The daily workflow audits the image actually published. A successful scan of a
corrected CI build does not update `latest`; release publication is a separate
existing workflow. Old published revisions or changed package/scope cannot reuse
the current Trixie exceptions.
