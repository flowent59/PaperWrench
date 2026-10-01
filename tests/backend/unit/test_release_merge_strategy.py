from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _check(repo: Path, base: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "check_release_merge_strategy.py"),
            base,
            "HEAD",
        ],
        cwd=repo,
        capture_output=True,
        text=True,
    )


def test_feature_commit_inside_merge_commit_is_reported(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Release test")
    _git(repo, "config", "user.email", "release-test@example.invalid")
    (repo / "base.txt").write_text("base", encoding="utf-8")
    _git(repo, "add", "base.txt")
    _git(repo, "commit", "-m", "chore: release 0.5.0")
    base = _git(repo, "rev-parse", "HEAD")

    _git(repo, "switch", "-c", "feature")
    (repo / "feature.txt").write_text("feature", encoding="utf-8")
    _git(repo, "add", "feature.txt")
    _git(repo, "commit", "-m", "feat(automation): schedule rules")
    _git(repo, "switch", "main")
    _git(
        repo,
        "merge",
        "--no-ff",
        "feature",
        "-m",
        "Merge pull request #80 from example/feature",
    )
    result = _check(repo, base)
    assert result.returncode == 1
    assert "Merge pull request #80 from example/feature" in result.stdout


def test_squash_style_commit_is_not_reported(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Release test")
    _git(repo, "config", "user.email", "release-test@example.invalid")
    (repo / "base.txt").write_text("base", encoding="utf-8")
    _git(repo, "add", "base.txt")
    _git(repo, "commit", "-m", "chore: release 0.5.0")
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "feature.txt").write_text("feature", encoding="utf-8")
    _git(repo, "add", "feature.txt")
    _git(repo, "commit", "-m", "feat(automation): schedule rules (#80)")
    result = _check(repo, base)
    assert result.returncode == 0
    assert "No duplicate-prone merge commits found" in result.stdout
