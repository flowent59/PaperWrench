"""Run pinned scanners outside a Docker image and retain evidence on every outcome."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import subprocess
import tarfile
import tempfile
import urllib.request
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any
from typing import cast

from image_security import AuditError
from image_security import evaluate
from image_security import fresh
from image_security import registry_digest
from image_security import require

BOOTSTRAP = "1cfb40cbf6187acc499f491fa24b7a56ae35efd6"
REGISTRY = "docs/security-audits/2026-10-04-residual/dispositions.json"
RUNTIME_PATHS = ["Dockerfile", ".dockerignore", "docker-compose.yml", "backend", "frontend"]
TOOLS = {
    "grype": (
        "0.120.0",
        "anchore/grype",
        "grype_0.120.0_linux_amd64.tar.gz",
        "a5a1218dce63acdac152a6b3b5bb366e7267e36f4069848cf455543b3fa5700e",
    ),
    "trivy": (
        "0.75.0",
        "aquasecurity/trivy",
        "trivy_0.75.0_Linux-64bit.tar.gz",
        "c6e65abddb348e25f10549df887045629cf28cc72453cd1c63acb717316b3f3f",
    ),
}


def command(args: list[str], **kwargs: Any) -> bytes:
    return cast(bytes, subprocess.check_output(args, timeout=900, **kwargs))


def git_file(ref: str, path: str) -> bytes:
    require(re.fullmatch(r"[a-f0-9]{40}", ref) is not None, "Approval must use an immutable commit")
    return command(["git", "show", f"{ref}:{path}"])


def runtime_tree(ref: str) -> str:
    entries = command(["git", "ls-tree", "-r", ref, "--", *RUNTIME_PATHS])
    require(bool(entries), "Missing source inventory")
    return hashlib.sha256(entries).hexdigest()


def approved_policy(ref: str) -> tuple[dict[str, Any], dict[str, Any]]:
    # The bootstrap is the reviewed/merged #100 commit, never this PR's new file.
    exists = (
        subprocess.run(
            ["git", "cat-file", "-e", f"{ref}:.security/reviewed-runtime.json"],
            capture_output=True,
            check=False,
        ).returncode
        == 0
    )
    if exists:
        approval = json.loads(git_file(ref, ".security/reviewed-runtime.json"))
    else:
        require(runtime_tree(ref) == runtime_tree(BOOTSTRAP), "Unreviewed bootstrap runtime")
        approval = {
            "schema_version": 1,
            "runtime_tree_sha256": runtime_tree(BOOTSTRAP),
            "registry_path": REGISTRY,
            "registry_sha256": registry_digest(git_file(BOOTSTRAP, REGISTRY)),
            "first_seen": {},
        }
    require(
        approval["schema_version"] == 1
        and re.fullmatch(
            r"docs/security-audits/[a-zA-Z0-9_-]+/dispositions\.json", approval["registry_path"]
        )
        is not None,
        "Unsupported approval policy",
    )
    require(
        approval.get("runtime_paths", RUNTIME_PATHS) == RUNTIME_PATHS,
        "Runtime input scope may not be narrowed",
    )
    raw = git_file(ref, approval["registry_path"])
    require(registry_digest(raw) == approval["registry_sha256"], "Unapproved registry modification")
    return approval, json.loads(raw)


def save(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def download(url: str, path: Path) -> None:
    require(url.startswith("https://"), "HTTPS required")
    request = urllib.request.Request(url, headers={"User-Agent": "paperwrench-image-audit/1.0"})
    with urllib.request.urlopen(request, timeout=120) as response, path.open("wb") as dest:
        while chunk := response.read(1024 * 1024):
            dest.write(chunk)


def install_tools(directory: Path, output: Path) -> dict[str, str]:
    versions = {}
    for name, (version, repository, asset, checksum) in TOOLS.items():
        archive = directory / asset
        download(f"https://github.com/{repository}/releases/download/v{version}/{asset}", archive)
        require(
            hashlib.sha256(archive.read_bytes()).hexdigest() == checksum,
            f"Invalid {name} release checksum",
        )
        with tarfile.open(archive) as source:
            member = source.getmember(name)
            require(member.isfile(), "Scanner binary is not a regular file")
            binary = source.extractfile(member)
            if binary is None:
                raise AuditError("Missing scanner binary")
            (directory / name).write_bytes(binary.read())
        (directory / name).chmod(0o755)
        flag = "--output" if name == "grype" else "--format"
        with (output / f"{name}-version.log").open("wb") as log:
            raw = command([str(directory / name), "version", flag, "json"], stderr=log)
        (output / f"{name}-version.json").write_bytes(raw)
        data = json.loads(raw)
        actual = data["version"] if name == "grype" else data["Version"]
        require(actual.removeprefix("v") == version, f"Unexpected {name} binary version")
        versions[name] = version
    return versions


def database_snapshot(directory: Path, output: Path, env: dict[str, str]) -> dict[str, Any]:
    latest = output / "grype-db-latest.json"
    download("https://grype.anchore.io/databases/v6/latest.json", latest)
    db = json.loads(latest.read_bytes())
    require(
        db["status"] == "active" and re.fullmatch(r"v6\.\d+\.\d+", db["schemaVersion"]) is not None,
        "Unsupported Grype DB manifest",
    )
    fresh(db["built"], datetime.now(UTC))
    require(
        re.fullmatch(r"vulnerability-db_v6\.\d+\.\d+_[0-9TZ:_-]+\.tar\.(gz|zst)", db["path"])
        is not None,
        "Invalid DB archive path",
    )
    archive = directory / "db.tar.gz"
    download("https://grype.anchore.io/databases/v6/" + db["path"], archive)
    require(
        "sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest() == db["checksum"],
        "Grype DB checksum mismatch",
    )
    with (output / "db-download.log").open("wb") as log:
        subprocess.run(
            [str(directory / "grype"), "db", "import", str(archive)],
            env=env,
            cwd=directory,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=300,
        )
        subprocess.run(
            [
                str(directory / "trivy"),
                "image",
                "--download-db-only",
                "--cache-dir",
                str(directory / "trivy-cache"),
                "--no-progress",
            ],
            env=env,
            cwd=directory,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=600,
        )
    trivy = json.loads((directory / "trivy-cache/db/metadata.json").read_bytes())
    require(trivy["Version"] == 2, "Unsupported Trivy DB schema")
    fresh(trivy["UpdatedAt"], datetime.now(UTC))
    metadata = {"grype_db": db, "trivy_db": trivy}
    save(output / "databases.json", metadata)
    return metadata


def probe_image(image: str, output: Path) -> dict[str, Any]:
    container = (
        command(
            [
                "docker",
                "create",
                "-i",
                "--read-only",
                "--tmpfs",
                "/tmp",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--network",
                "none",
                "--entrypoint",
                "python",
                image,
                "-",
            ]
        )
        .decode()
        .strip()
    )
    try:
        inspection = json.loads(command(["docker", "inspect", container]))[0]
        save(output / "container.json", inspection)
        with (output / "probe.log").open("wb") as log:
            raw = command(
                ["docker", "start", "-ai", container],
                input=Path(__file__).with_name("probe_image_security.py").read_bytes(),
                stderr=log,
            )
        (output / "inventory.json").write_bytes(raw)
        probe = json.loads(raw)
        require(bool(probe["packages_tsv"] and probe["python_packages"]), "Empty image inventory")
        return {"container": inspection, "probe": probe}
    finally:
        subprocess.run(["docker", "rm", "-fv", container], capture_output=True, check=False)


def scan(args: argparse.Namespace, output: Path) -> dict[str, Any]:
    approval, registry = approved_policy(args.approved_ref)
    save(output / "approved-policy.json", approval)
    save(output / "approved-dispositions.json", registry)
    image = args.image
    registry_digest_value = None
    if args.published:
        require(image == "ghcr.io/flowent59/paperwrench:latest", "Unsupported published target")
        digest = (
            command(
                [
                    "docker",
                    "buildx",
                    "imagetools",
                    "inspect",
                    image,
                    "--format",
                    "{{.Manifest.Digest}}",
                ]
            )
            .decode()
            .strip()
        )
        require(re.fullmatch(r"sha256:[a-f0-9]{64}", digest) is not None, "Invalid registry digest")
        image = "ghcr.io/flowent59/paperwrench@" + digest
        registry_digest_value = digest
        command(["docker", "pull", "--platform", "linux/amd64", image])
    inspection = json.loads(command(["docker", "image", "inspect", image]))[0]
    save(output / "image.json", inspection)
    image = inspection["Id"]
    revision = inspection["Config"]["Labels"].get("org.opencontainers.image.revision", "")
    require(re.fullmatch(r"[a-f0-9]{40}", revision) is not None, "Missing immutable OCI revision")
    if subprocess.run(
        ["git", "cat-file", "-e", revision], capture_output=True, check=False
    ).returncode:
        command(["git", "fetch", "--no-tags", "origin", revision])
    metadata = {
        "image": inspection,
        "source_commit": revision,
        "approved_ref": args.approved_ref,
        "registry_digest": registry_digest_value,
        "runtime_tree_sha256": runtime_tree(revision),
        "base_images": [
            line
            for line in git_file(revision, "Dockerfile").decode().splitlines()
            if line.startswith("FROM ")
        ],
        "started_at": datetime.now(UTC).isoformat(),
    }
    save(output / "metadata.json", metadata)
    with tempfile.TemporaryDirectory(prefix="paperwrench-scanners-") as work:
        directory = Path(work).resolve()
        # No repo-level scanner configuration, ignore/VEX files or application secrets.
        env = {
            key: os.environ[key]
            for key in ("PATH", "HOME", "DOCKER_HOST", "DOCKER_CONFIG")
            if key in os.environ
        }
        env.update(
            GRYPE_DB_CACHE_DIR=str(directory / "grype-cache"),
            GRYPE_DB_AUTO_UPDATE="false",
            GRYPE_CHECK_FOR_APP_UPDATE="false",
        )
        (directory / "config.yaml").write_text("{}\n", encoding="utf-8")
        (directory / "ignore").write_text("", encoding="utf-8")
        metadata["tools"] = install_tools(directory, output)
        metadata["tool_releases"] = TOOLS
        save(output / "metadata.json", metadata)
        metadata.update(database_snapshot(directory, output, env))
        metadata["scanner_exit_codes"] = {}
        commands = {
            "grype": [
                str(directory / "grype"),
                "docker:" + image,
                "--config",
                str(directory / "config.yaml"),
                "--output",
                "json",
            ],
            "trivy": [
                str(directory / "trivy"),
                "image",
                "--image-src",
                "docker",
                "--config",
                str(directory / "config.yaml"),
                "--ignorefile",
                str(directory / "ignore"),
                "--cache-dir",
                str(directory / "trivy-cache"),
                "--skip-db-update",
                "--skip-java-db-update",
                "--scanners",
                "vuln",
                "--list-all-pkgs",
                "--format",
                "json",
                "--no-progress",
                image,
            ],
        }
        for name, invocation in commands.items():
            with (
                (output / f"{name}.json").open("wb") as report,
                (output / f"{name}.log").open("wb") as log,
            ):
                try:
                    completed = subprocess.run(
                        invocation,
                        cwd=directory,
                        env=env,
                        stdout=report,
                        stderr=log,
                        check=False,
                        timeout=900,
                    )
                    metadata["scanner_exit_codes"][name] = completed.returncode
                except subprocess.TimeoutExpired:
                    log.write(b"Scanner timed out after 900 seconds\n")
                    metadata["scanner_exit_codes"][name] = 124
            save(output / "metadata.json", metadata)
        metadata.update(probe_image(image, output))
    save(output / "metadata.json", metadata)
    return evaluate(
        json.loads((output / "grype.json").read_bytes()),
        json.loads((output / "trivy.json").read_bytes()),
        metadata,
        registry,
        approval,
        datetime.now(UTC),
    )


def write_result(output: Path, result: dict[str, Any]) -> None:
    save(output / "result.json", result)
    lines = [
        "## Runtime image security audit",
        "",
        f"Status: **{result['status']}**",
        f"Exit code: {result['exit_code']}",
        "",
        f"Occurrences: {result.get('occurrences', 'unavailable')}; "
        f"identities: {result.get('identities', 'unavailable')}; "
        f"blocking: {result.get('blocked', 'unavailable')}",
        "",
    ]
    lines.extend(html.escape(error) for error in result.get("errors", []))
    lines += [
        "",
        "| Advisory | Scanner/package/version | Severity | Decision | Expiry | Follow-up |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in result.get("findings", []):
        values = [
            row["identity"],
            f"{row['scanner']}: {row['package']} {row['version']}",
            row["maximum_severity"],
            (row["disposition"] + ": " if row["disposition"] else "") + row["reason"],
            row["expires_at"] or "—",
            str(row["followup_issue"] or "—"),
        ]
        lines.append(
            "| "
            + " | ".join(
                re.sub(r"([\\`*_{}\[\]()#+.!-])", r"\\\1", html.escape(value))
                .replace("|", "&#124;")
                .replace("\n", " ")
                for value in values
            )
            + " |"
        )
    (output / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    checksums = [
        hashlib.sha256(path.read_bytes()).hexdigest() + "  " + path.name
        for path in sorted(output.iterdir())
        if path.is_file() and path.name != "SHA256SUMS"
    ]
    (output / "SHA256SUMS").write_text("\n".join(checksums) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--approved-ref", required=True)
    parser.add_argument("--published", action="store_true")
    parser.add_argument("--output", default="image-audit")
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    try:
        result = scan(args, output)
    except (
        AuditError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        IndexError,
        subprocess.SubprocessError,
    ) as exc:
        result = {"exit_code": 2, "status": "technical-error", "errors": [str(exc)], "findings": []}
    write_result(output, result)
    print(f"Image audit: {result['status']}; evidence: {output}")
    return int(result["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
