"""The release gate must see drafts and refuse unrelated tags or commits."""

from __future__ import annotations

from typing import Any

import pytest

from release_gate import API_ROOT
from release_gate import GitHubAPI
from release_gate import ReleaseGateError
from release_gate import ReleaseNotFound
from release_gate import check_release
from release_gate import find_release
from release_gate import publish
from release_gate import require_context

TAG = "v0.1.0"
COMMIT = "cc9a744480ac33f0b473c38c11ee358f55766074"


def release(*, draft: bool = True) -> dict[str, Any]:
    return {
        "id": 397206521,
        "url": f"https://api.github.com{API_ROOT}/releases/397206521",
        "tag_name": TAG,
        "target_commitish": COMMIT,
        "draft": draft,
        "body": "## 0.1.0\n\nRelease Please notes",
    }


class FakeAPI(GitHubAPI):
    def __init__(self, releases: list[dict[str, Any]], remote_commit: str = COMMIT) -> None:
        super().__init__("test-token")
        self.releases = releases
        self.remote_commit = remote_commit
        self.patches = 0

    def call(self, method: str, path: str, body: dict[str, object] | None = None) -> Any:
        # The published-by-tag endpoint returns 404 for a draft. A regression
        # to that endpoint must fail this test, even when a draft exists.
        assert "/releases/tags/" not in path
        if method == "GET" and path.startswith(f"{API_ROOT}/releases?per_page="):
            return self.releases
        if method == "GET" and path == f"{API_ROOT}/git/ref/tags/{TAG}":
            return {
                "ref": f"refs/tags/{TAG}",
                "object": {"type": "commit", "sha": self.remote_commit},
            }
        if path == f"{API_ROOT}/releases/397206521":
            if method == "GET":
                return self.releases[0]
            if method == "PATCH":
                assert body == {"draft": False}
                self.releases[0]["draft"] = False
                self.patches += 1
                return self.releases[0]
        raise AssertionError(f"Unexpected API call: {method} {path}")


@pytest.mark.parametrize("draft", [True, False])
def test_exact_draft_or_published_release_is_accepted(draft: bool) -> None:
    api = FakeAPI([release(draft=draft)])
    assert check_release(api, find_release(api, TAG), TAG, COMMIT) == 397206521


def test_arbitrary_tag_without_release_is_blocked() -> None:
    with pytest.raises(ReleaseNotFound, match="No draft or published Release"):
        find_release(FakeAPI([]), TAG)


@pytest.mark.parametrize("field,value", [("tag_name", "v0.2.0"), ("target_commitish", "a" * 40)])
def test_release_must_match_tag_and_commit(field: str, value: str) -> None:
    candidate = release()
    candidate[field] = value
    with pytest.raises(ReleaseGateError):
        check_release(FakeAPI([candidate]), candidate, TAG, COMMIT)


def test_release_must_belong_to_repository() -> None:
    candidate = release()
    candidate["url"] = "https://api.github.com/repos/other/repo/releases/397206521"
    with pytest.raises(ReleaseGateError, match="another repository"):
        check_release(FakeAPI([candidate]), candidate, TAG, COMMIT)


def test_remote_tag_must_still_point_to_expected_commit() -> None:
    with pytest.raises(ReleaseGateError, match="Remote tag"):
        check_release(FakeAPI([release()], remote_commit="a" * 40), release(), TAG, COMMIT)


def test_publishing_draft_is_idempotent() -> None:
    api = FakeAPI([release()])
    publish(api, 397206521, TAG, COMMIT)
    publish(api, 397206521, TAG, COMMIT)
    assert api.patches == 1
    assert api.releases[0]["draft"] is False


def test_recovery_requires_the_expected_repository() -> None:
    with pytest.raises(ReleaseGateError, match="Unexpected repository"):
        require_context("other/repo", TAG, COMMIT)
