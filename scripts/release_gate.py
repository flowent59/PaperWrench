"""Gate GHCR publication on the exact Release Please release and Git tag."""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.error import URLError
from urllib.request import Request
from urllib.request import urlopen

REPOSITORY = "flowent59/PaperWrench"
API_ROOT = f"/repos/{REPOSITORY}"


class ReleaseGateError(Exception):
    """A release cannot safely be published."""


class ReleaseNotFound(ReleaseGateError):
    """The tag's draft or published release is not visible yet."""


class GitHubAPI:
    def __init__(self, token: str) -> None:
        if not token:
            raise ReleaseGateError("RELEASE_PLEASE_TOKEN is required to read draft releases")
        self.token = token

    def call(self, method: str, path: str, body: dict[str, object] | None = None) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        request = Request(
            f"https://api.github.com{path}",
            data=data,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "User-Agent": "PaperWrench-release-gate",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urlopen(request, timeout=20) as response:
                return json.load(response)
        except HTTPError as error:
            raise ReleaseGateError(
                f"GitHub API returned HTTP {error.code} for {method} {path}; "
                "check RELEASE_PLEASE_TOKEN and repository access"
            ) from error
        except URLError as error:
            raise ReleaseGateError(f"GitHub API unavailable for {method} {path}") from error


def require_context(repository: str, tag: str, commit: str) -> None:
    if repository.casefold() != REPOSITORY.casefold():
        raise ReleaseGateError(f"Unexpected repository: {repository!r}")
    if not re.fullmatch(r"v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", tag):
        raise ReleaseGateError(f"Invalid release tag: {tag!r}")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ReleaseGateError(f"Invalid expected commit: {commit!r}")


def find_release(api: GitHubAPI, tag: str) -> dict[str, Any]:
    matches: list[dict[str, Any]] = []
    for page in range(1, 21):
        releases = api.call("GET", f"{API_ROOT}/releases?per_page=100&page={page}")
        if not isinstance(releases, list):
            raise ReleaseGateError("GitHub did not return a release list")
        for release in releases:
            if isinstance(release, dict) and release.get("tag_name") == tag:
                matches.append(release)
        if len(releases) < 100:
            break
    else:
        raise ReleaseGateError("Release list exceeded 2000 entries; refusing an incomplete search")
    if not matches:
        raise ReleaseNotFound(
            f"No draft or published Release for {tag}; the PAT needs push access to see drafts"
        )
    if len(matches) != 1:
        raise ReleaseGateError(f"Expected one Release for {tag}, found {len(matches)}")
    return matches[0]


def tag_commit(api: GitHubAPI, tag: str) -> str:
    reference = api.call("GET", f"{API_ROOT}/git/ref/tags/{tag}")
    if not isinstance(reference, dict) or reference.get("ref") != f"refs/tags/{tag}":
        raise ReleaseGateError(f"GitHub did not return the exact ref for {tag}")
    target = reference.get("object")
    for _ in range(5):
        if not isinstance(target, dict):
            raise ReleaseGateError(f"Malformed Git ref for {tag}")
        sha = target.get("sha")
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise ReleaseGateError(f"Malformed Git object for {tag}")
        if target.get("type") == "commit":
            return sha
        if target.get("type") != "tag":
            raise ReleaseGateError(f"Tag {tag} does not resolve to a commit")
        annotated = api.call("GET", f"{API_ROOT}/git/tags/{sha}")
        target = annotated.get("object") if isinstance(annotated, dict) else None
    raise ReleaseGateError(f"Too many nested annotated tags for {tag}")


def check_release(api: GitHubAPI, release: dict[str, Any], tag: str, commit: str) -> int:
    release_id = release.get("id")
    if not isinstance(release_id, int) or release_id <= 0:
        raise ReleaseGateError("Release has no valid numeric ID")
    expected_url = f"https://api.github.com{API_ROOT}/releases/{release_id}"
    url = release.get("url")
    if not isinstance(url, str) or url.casefold() != expected_url.casefold():
        raise ReleaseGateError("Release belongs to another repository")
    if release.get("tag_name") != tag:
        raise ReleaseGateError(f"Release {release_id} has the wrong tag")
    if release.get("target_commitish") != commit:
        raise ReleaseGateError(f"Release {release_id} does not target the tagged commit")
    if not isinstance(release.get("draft"), bool):
        raise ReleaseGateError(f"Release {release_id} has no draft state")
    if not isinstance(release.get("body"), str) or not release["body"].strip():
        raise ReleaseGateError(f"Release {release_id} has no release notes")
    if tag_commit(api, tag) != commit:
        raise ReleaseGateError(f"Remote tag {tag} does not point to the expected commit")
    return release_id


def verify(api: GitHubAPI, tag: str, commit: str) -> int:
    for attempt in range(12):
        try:
            release = find_release(api, tag)
        except ReleaseNotFound:
            if attempt == 11:
                raise
            time.sleep(5)
            continue
        return check_release(api, release, tag, commit)
    raise AssertionError("Unreachable release lookup state")


def publish(api: GitHubAPI, release_id: int, tag: str, commit: str) -> None:
    release = api.call("GET", f"{API_ROOT}/releases/{release_id}")
    if not isinstance(release, dict):
        raise ReleaseGateError("GitHub did not return a Release")
    check_release(api, release, tag, commit)
    if not release["draft"]:
        print(f"Release {release_id} is already published")
        return
    api.call("PATCH", f"{API_ROOT}/releases/{release_id}", {"draft": False})
    updated = api.call("GET", f"{API_ROOT}/releases/{release_id}")
    if not isinstance(updated, dict):
        raise ReleaseGateError("GitHub did not return the updated Release")
    check_release(api, updated, tag, commit)
    if updated["draft"]:
        raise ReleaseGateError(f"Release {release_id} remained a draft after publication")
    print(f"Published Release {release_id} for {tag} at {commit}")


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in ("verify", "publish"):
        raise ReleaseGateError("Usage: release_gate.py verify|publish")
    tag = os.environ.get("RELEASE_TAG", "")
    commit = os.environ.get("EXPECTED_COMMIT", "")
    require_context(os.environ.get("GITHUB_REPOSITORY", ""), tag, commit)
    api = GitHubAPI(os.environ.get("RELEASE_PLEASE_TOKEN", ""))
    if sys.argv[1] == "verify":
        release_id = verify(api, tag, commit)
        output = os.environ.get("GITHUB_OUTPUT")
        if not output:
            raise ReleaseGateError("GITHUB_OUTPUT is required for the release gate")
        with Path(output).open("a", encoding="utf-8") as stream:
            stream.write(f"release_id={release_id}\n")
        print(f"Verified Release {release_id} for {tag} at {commit}")
    else:
        raw_release_id = os.environ.get("RELEASE_ID", "")
        if not raw_release_id.isdecimal():
            raise ReleaseGateError("RELEASE_ID must be a numeric API release ID")
        publish(api, int(raw_release_id), tag, commit)


if __name__ == "__main__":
    try:
        main()
    except ReleaseGateError as error:
        raise SystemExit(f"Release gate failed: {error}") from error
