# Release process

Release Please owns the version decision, the release PR, `CHANGELOG.md`, the
`vX.Y.Z` Git tag and the GitHub Release. The application has one release stream:
the root entry in `.release-please-manifest.json` records the last released
version. The backend, frontend package and lockfile, Compose image defaults and
`.env.example` are updated in the generated release PR through
`release-please-config.json`. The historical release overview under
`docs/releases/` is context; the generated changelog is the release notes source.

## First release and version rules

The manifest starts at `0.0.0`, meaning no version has been published. The
first Release Please PR targets **0.1.0**. The bootstrap SHA makes its changelog
start after the repository's initial commit, so the MVP's Conventional Commits
are included. Merging this preparation PR only enables the automation; it does
not create a tag, image or GitHub Release. Review and merge the generated
`chore(main): release ...` PR to publish.

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
then publishes one `linux/amd64` GHCR image under the exact version and
`latest`. It pulls the remote image into a disposable Paperless 3.2.1 stack and
checks migration, network connection, direct HTTP, SPA routes, persistence and
token redaction. After these checks pass, the workflow publishes the draft
GitHub Release with Release Please's generated notes. If a version tag already
exists with the same source commit, a rerun validates it without rebuilding or
republishing; a different source commit fails. Only the release workflow writes
GHCR. A tag without an existing
GitHub Release cannot publish. The existing CI remains the merge gate.

The release build uses the exact tagged commit, pinned Docker base digests,
the frontend npm lockfile, pinned wheel tooling and
`backend/requirements.lock` for the Linux amd64 runtime. Its published digest
is the exact deployment identity. Update dependency pins deliberately in a
normal reviewed PR when security or compatibility requires it.

GitHub's repository `GITHUB_TOKEN` normally suppresses downstream workflow
runs for tags it creates. `RELEASE_PLEASE_TOKEN` must be a fine-grained PAT
that is **not** `GITHUB_TOKEN`; otherwise the tag push will not start the Docker
workflow. The release workflow itself uses its
short-lived `GITHUB_TOKEN`, scoped to `contents:read` and `packages:write`.

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
   upload, then rerun the failed tag workflow. That workflow checks anonymous
   pull access before declaring the image ready. It skips the build if the
   same version was already uploaded.

Release Please's draft Release remains unpublished if the image check fails.
A failed check after the image was uploaded can be retried without republishing
the version. If the tagged source itself needs a correction, ship a new version;
released tags are immutable.
