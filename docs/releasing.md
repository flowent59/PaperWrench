# Release process

Release Please owns the version decision, the release PR, `CHANGELOG.md`, the
`vX.Y.Z` Git tag and the GitHub Release. The application has one release stream:
the root entry in `.release-please-manifest.json` records the last released
version. The backend, frontend package and lockfile, Compose image defaults and
`.env.example` are updated in the generated release PR through
`release-please-config.json`. The historical release overview under
`docs/releases/` is context; the generated changelog is the release notes source.

## First release and version rules

The manifest started at `0.0.0`, meaning no version had been published. The
first Release Please PR targeted **0.1.0**. The bootstrap SHA made its changelog
start after the repository's initial commit, so the MVP's Conventional Commits
were included. The generated `chore(main): release 0.1.0` PR has been merged;
the tag and draft GitHub Release exist, while image publication is pending.

Use Conventional Commit titles for all PRs, because squash merges put the PR
title on `main`. Examples: `feat: add a view`, `fix(api): handle a 403`,
`docs: explain backup`, `feat!: change an API contract`. Explain breaking
changes in a `BREAKING CHANGE:` footer. CI rejects titles outside the
convention. For the 0.x series, breaking changes advance the minor version,
features advance the minor version and fixes advance the patch version. Do not
edit version numbers, create tags or write release notes by hand. Review the
generated release PR and its CI before merging it.

After the release PR is merged, Release Please creates the tag and a draft
GitHub Release using `RELEASE_PLEASE_TOKEN`. A `v*` tag push starts `release.yml`,
which verifies the tag, the main ancestry and matching application versions,
then uses the Release Please token to find a draft or published Release through
the paginated GitHub API. The Release must belong to this repository, have the
exact tag and notes, and target the same commit as the remote Git tag. It
publishes one `linux/amd64` GHCR image under the exact version and
`latest`. It pulls the remote image into a disposable Paperless-ngx `latest` stack and
checks migration, network connection, direct HTTP, SPA routes, persistence and
token redaction. After these checks pass, the workflow publishes the draft
GitHub Release with Release Please's generated notes. If a version tag already
exists with the same source commit, a rerun validates it without rebuilding or
republishing; a different source commit fails. Only the release workflow writes
GHCR. A tag without a matching GitHub Release cannot publish. The existing CI
remains the merge gate.

The release build uses the exact tagged commit, pinned Docker base digests,
the frontend npm lockfile, pinned wheel tooling and
`backend/requirements.lock` for the Linux amd64 runtime. Its published digest
is the exact deployment identity. Update dependency pins deliberately in a
normal reviewed PR when security or compatibility requires it.

GitHub's repository `GITHUB_TOKEN` normally suppresses downstream workflow
runs for tags it creates. `RELEASE_PLEASE_TOKEN` must be a fine-grained PAT
that is **not** `GITHUB_TOKEN`; otherwise the tag push will not start the Docker
workflow. The release workflow uses that PAT to inspect draft Releases and
publish one only after image validation. Its short-lived `GITHUB_TOKEN` has
`contents:read`, with `packages:write` only in the Docker publication job.
GitHub does not expose draft Releases to a read-only token. The by-tag REST
endpoint only returns published Releases.

## Recovering the first 0.1.0 publication

The first tag run failed before any image upload because its read-only token
could not see the draft Release. Re-running that old Actions run keeps the
workflow at the original tagged commit, including the broken lookup. After
merging the corrective PR, open **Actions > Release > Run workflow**, select
the **main** branch and enter `v0.1.0` as the existing tag. The manual run
checks out the reviewed workflow tools from `main` and builds only the source
at `v0.1.0`. It confirms that the remote tag and Release both target
`cc9a744480ac33f0b473c38c11ee358f55766074`, and that the root manifest
still names 0.1.0 before moving `latest`. It does not create or move the tag.
If the version image already exists at that commit, the run skips the build;
if the manifest has advanced to a later version, it refuses to publish the
older image or move `latest` back. The versioned image, remote Paperless smoke
test and draft Release publication then follow the normal path. The first GHCR
package may need to be made public and the **new manual run** started again
before its anonymous-pull check can pass.

## Repository setup

1. Add an Actions secret `RELEASE_PLEASE_TOKEN` with access to this repository
   and Contents, Pull requests and Issues **read/write** permissions. Issues
   permission allows Release Please to manage its lifecycle labels. Restrict
   the token to this repository. The workflow fails when the secret is missing.
2. In **Settings > Actions > General > Workflow permissions**, enable
   **Allow GitHub Actions to create and approve pull requests**. Allow Actions
   to run `googleapis/release-please-action`, Docker's official actions and
   the existing CI actions if the repository uses an action allowlist.
3. Protect `main` with the existing CI required checks and review the generated
   release PR. Prefer squash merges so PR titles become Conventional Commits.
4. The first GHCR package may initially be private. Set
   `flowent59/paperwrench` to public in the package settings after its first
   upload, then rerun the release workflow. That workflow checks anonymous
   pull access before declaring the image ready. It skips the build if the
   same version was already uploaded.

Release Please's draft Release remains unpublished if the image check fails.
A failed check after the image was uploaded can be retried without republishing
the version. If the tagged source itself needs a correction, ship a new version;
released tags are immutable.
