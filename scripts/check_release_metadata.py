"""Check the single-version release contract without importing the application."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tomllib
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    config = json.loads((root / "release-please-config.json").read_text(encoding="utf-8"))
    manifest = json.loads((root / ".release-please-manifest.json").read_text(encoding="utf-8"))
    assert list(config["packages"]) == ["."], "Only one releasable application is expected"
    assert list(manifest) == ["."], "Only one release version should be tracked"
    app_version = tomllib.loads((root / "backend/pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]["version"]
    package = json.loads((root / "frontend/package.json").read_text(encoding="utf-8"))
    lock = json.loads((root / "frontend/package-lock.json").read_text(encoding="utf-8"))
    source = (root / "backend/src/paperwrench/__init__.py").read_text(encoding="utf-8")
    match = re.search(
        r'^__version__ = "([^"]+)"\s+# x-release-please-version$', source, re.MULTILINE
    )
    assert match, "Python application version is not marked for Release Please"
    assert re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", app_version)
    assert (
        package["version"]
        == lock["version"]
        == lock["packages"][""]["version"]
        == match.group(1)
        == app_version
    )
    assert manifest["."] in (app_version, "0.0.0"), "Manifest and application versions disagree"
    if manifest["."] == "0.0.0":
        assert app_version == config["packages"]["."]["initial-version"] == "0.1.0"
    base_sha = os.environ.get("PR_BASE_SHA")
    if base_sha:
        try:
            base_manifest = json.loads(
                subprocess.check_output(
                    ["git", "show", f"{base_sha}:.release-please-manifest.json"],
                    cwd=root,
                    text=True,
                    stderr=subprocess.DEVNULL,
                )
            )
        except subprocess.CalledProcessError:
            base_manifest = None
        if base_manifest and base_manifest["."] == "0.0.0" and manifest["."] != "0.0.0":
            assert manifest["."] == "0.1.0", "First release must be 0.1.0"
    for entry in config["packages"]["."]["extra-files"]:
        path = root / entry["path"]
        assert path.is_file(), f"Release Please extra file is missing: {path}"
        if entry["type"] == "generic":
            assert "x-release-please-version" in path.read_text(encoding="utf-8")
    for compose in ("docker-compose.paperless.yml", "docker-compose.yml"):
        content = (root / compose).read_text(encoding="utf-8")
        assert f"ghcr.io/flowent59/paperwrench:{app_version}" in content
        assert "pull_policy: always" in content and "    build:" not in content
    assert "paperwrench:local" in (root / "docker-compose.build.yml").read_text(
        encoding="utf-8"
    )
    assert (root / "CHANGELOG.md").is_file() and (root / "LICENSE").is_file()
    assert (
        tomllib.loads((root / "backend/pyproject.toml").read_text(encoding="utf-8"))["project"][
            "license"
        ]["text"]
        == "GPL-3.0-or-later"
    )
    title = os.environ.get("PR_TITLE")
    if title:
        assert re.fullmatch(
            r"(feat|fix|docs|perf|refactor|test|build|ci|chore|revert)(\([a-z0-9._/-]+\))?!?: .+",
            title,
        ), (
            "PR title must follow Conventional Commits, e.g. feat: add a view "
            "or fix(api): correct an error"
        )
    print(f"Release metadata OK: {app_version}, manifest {manifest['.']}")


if __name__ == "__main__":
    main()
