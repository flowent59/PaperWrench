"""Reject merge commits that make Release Please count a change twice."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys

RELEASABLE_COMMIT = re.compile(r"^(?:feat|fix|perf)(?:\([^\n)]+\))?!?:\s+\S")


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def duplicate_merge_commits(base: str, head: str) -> list[tuple[str, str]]:
    """Return merges with releasable Conventional Commits on their side branch."""
    merge_shas = git("rev-list", "--merges", f"{base}..{head}").splitlines()
    duplicates: list[tuple[str, str]] = []
    for merge_sha in merge_shas:
        parents = git("rev-list", "--parents", "-n", "1", merge_sha).split()
        if len(parents) < 3:
            continue
        _, first_parent, second_parent = parents[:3]
        side_commits = git(
            "rev-list", second_parent, f"^{first_parent}"
        ).splitlines()
        for commit_sha in side_commits:
            subject = git("show", "-s", "--format=%s", commit_sha)
            if RELEASABLE_COMMIT.match(subject):
                merge_subject = git("show", "-s", "--format=%s", merge_sha)
                duplicates.append((merge_sha, merge_subject))
                break
    return duplicates


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check that the unreleased history uses squash merges."
    )
    parser.add_argument("base", help="last release tag")
    parser.add_argument("head", help="commit to check")
    args = parser.parse_args()
    try:
        duplicates = duplicate_merge_commits(args.base, args.head)
    except subprocess.CalledProcessError as exc:
        print(exc.stderr or "Unable to inspect git history", file=sys.stderr)
        return 2
    if duplicates:
        print(
            "Release Please may duplicate changelog entries for merge commits. "
            "Use squash merging; found:"
        )
        for sha, subject in duplicates:
            print(f"  {sha[:12]} {subject}")
        return 1
    print(f"No duplicate-prone merge commits found in {args.base}..{args.head}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
