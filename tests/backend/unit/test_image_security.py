"""Exercise the gate using the retained real #100 reports and adverse changes."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import zipfile
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from image_security import evaluate
from scan_image import BOOTSTRAP
from scan_image import approved_policy
from scan_image import write_result

Audit = tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]

ROOT = Path(__file__).resolve().parents[3]
NOW = datetime(2026, 10, 4, 15, tzinfo=UTC)


@pytest.fixture
def audit() -> Audit:
    with zipfile.ZipFile(ROOT / "docs/security-audits/2026-10-04-residual/audit-evidence.zip") as z:

        def read(name: str) -> Any:
            return json.loads(z.read("audit/" + name))

        grype, trivy = read("candidate-grype.json"), read("candidate-trivy.json")
        image = read("candidate-inspect.json")[0]
        probe = read("candidate-runtime-evidence.json")
        scope = read("filesystem-scope.json")
        probe["usrmerge"] = scope["usrmerge"]
        probe["node_modules"] = scope["node_modules"]
        approval = json.loads((ROOT / ".security/reviewed-runtime.json").read_bytes())
        metadata = {
            "image": image,
            "source_commit": image["Config"]["Labels"]["org.opencontainers.image.revision"],
            "scanner_exit_codes": {"grype": 0, "trivy": 0},
            "tools": {"grype": "0.120.0", "trivy": "0.75.0"},
            "grype_db": read("grype-db-latest.json"),
            "trivy_db": read("trivy-db-metadata.json"),
            "container": read("smoke-container-inspect.json"),
            "probe": probe,
            "runtime_tree_sha256": approval["runtime_tree_sha256"],
        }
    registry = json.loads(
        (ROOT / "docs/security-audits/2026-10-04-residual/dispositions.json").read_bytes()
    )
    return grype, trivy, metadata, registry, approval


def test_reviewed_real_reports_pass_without_hiding_findings(
    audit: Audit,
) -> None:
    result = evaluate(*audit, NOW)
    assert result["exit_code"] == 0, result
    assert result["identities"] == 80 and result["occurrences"] == 333
    assert all(
        row["exception"] and row["expires_at"] and row["followup_issue"]
        for row in result["findings"]
    )
    assert {row["disposition"] for row in result["findings"]} == {
        "code-not-shipped",
        "not-applicable-architecture",
        "mitigated",
        "accepted-risk",
    }


@pytest.mark.parametrize("severity", ["Critical", "High", "Unknown", "UNRECOGNIZED"])
def test_unreviewed_threshold_and_unfixed_high_block(
    audit: Audit,
    severity: str,
) -> None:
    grype, *_ = audit
    finding = copy.deepcopy(grype["matches"][0])
    finding["vulnerability"].update(
        id="CVE-2099-9999", severity=severity, fix={"versions": [], "state": "not-fixed"}
    )
    grype["matches"].append(finding)
    result = evaluate(*audit, NOW)
    assert result["exit_code"] == 1
    row = next(row for row in result["findings"] if row["identity"] == "CVE-2099-9999")
    assert row["blocking"] and not row["exception"]


@pytest.mark.parametrize("change", ["version", "path", "severity", "fix", "type"])
def test_exact_occurrences_require_revalidation(audit: Audit, change: str) -> None:
    match = audit[0]["matches"][0]
    if change == "version":
        match["artifact"]["version"] += "+changed"
    elif change == "path":
        match["artifact"]["locations"][0]["path"] = "/new/path"
    elif change == "severity":
        match["vulnerability"]["severity"] = "Critical"
    elif change == "fix":
        match["vulnerability"]["fix"]["versions"] = ["supported-new-correction"]
    else:
        match["artifact"]["type"] = "unknown-type"
    result = evaluate(*audit, NOW)
    assert result["exit_code"] == 1
    assert result["findings"][0]["blocking"] and not result["findings"][0]["exception"]


@pytest.mark.parametrize(
    "change",
    [
        "runtime",
        "architecture",
        "root",
        "writable",
        "privileged",
        "installer",
        "sqlite",
        "perl",
        "tool",
    ],
)
def test_runtime_and_deployment_changes_invalidate_decisions(
    audit: Audit,
    change: str,
) -> None:
    metadata = audit[2]
    probe = metadata["probe"]
    if change == "runtime":
        metadata["runtime_tree_sha256"] = "changed"
    elif change == "architecture":
        probe["architecture"] = "ppc64le"
    elif change == "root":
        probe["uid"] = 0
    elif change == "writable":
        metadata["container"]["HostConfig"]["ReadonlyRootfs"] = False
    elif change == "privileged":
        probe["privileged_files"] = ["/usr/bin/mount"]
    elif change == "installer":
        probe["installer_imports"]["pip"] = True
    elif change == "sqlite":
        probe["sqlite_symbols"]["sqlite3changeset_apply_v3"] = True
    elif change == "perl":
        probe["perl_modules"]["Pod::Text"]["importable"] = True
    else:
        probe["tools"]["bzip2recover"] = "/usr/bin/bzip2recover"
    assert evaluate(*audit, NOW)["exit_code"] != 0


@pytest.mark.parametrize(
    "change",
    [
        "exit",
        "json",
        "missing-db",
        "stale",
        "future",
        "schema",
        "identity",
        "layer",
        "ignored",
        "tool",
    ],
)
def test_technical_failures_are_distinct_from_findings(
    audit: Audit,
    change: str,
) -> None:
    grype, trivy, metadata, *_ = audit
    if change == "exit":
        metadata["scanner_exit_codes"]["grype"] = 2
    elif change == "json":
        del grype["matches"]
    elif change == "missing-db":
        del metadata["trivy_db"]
    elif change in ("stale", "future"):
        metadata["trivy_db"]["UpdatedAt"] = (
            NOW + timedelta(hours=1 if change == "future" else -25)
        ).isoformat()
    elif change == "schema":
        trivy["SchemaVersion"] = 999
    elif change == "identity":
        trivy["Metadata"]["ImageID"] = "sha256:" + "0" * 64
    elif change == "layer":
        trivy["Metadata"]["DiffIDs"] = []
    elif change == "ignored":
        grype["ignoredMatches"] = [grype["matches"][0]]
    else:
        metadata["tools"]["grype"] = "unverified"
    result = evaluate(*audit, NOW)
    assert result["exit_code"] == 2 and result["status"] == "technical-error"
    assert result["errors"]


def test_expired_lower_severity_decision_is_global_policy_error(
    audit: Audit,
) -> None:
    record = next(
        record for record in audit[3]["records"] if record["maximum_scanner_severity"] == "Low"
    )
    record["expires_at"] = NOW.isoformat()
    result = evaluate(*audit, NOW)
    assert result["exit_code"] == 2 and result["status"] == "policy-error"
    assert any(record["id"] in error for error in result["errors"])


@pytest.mark.parametrize("age,expected", [(None, 1), (0, 0), (29, 0), (30, 1), (-1, 2)])
def test_lower_severity_first_discovery_deadline(
    audit: Audit,
    age: int | None,
    expected: int,
) -> None:
    finding = copy.deepcopy(audit[0]["matches"][0])
    finding["vulnerability"].update(id="CVE-2099-9999", severity="Low", fix={"versions": []})
    audit[0]["matches"].append(finding)
    if age is not None:
        audit[4]["first_seen"]["CVE-2099-9999"] = (NOW - timedelta(days=age)).isoformat()
    assert evaluate(*audit, NOW)["exit_code"] == expected


def test_pr_files_cannot_approve_themselves() -> None:
    approval, registry = approved_policy(BOOTSTRAP)
    assert len(registry["records"]) == 80
    assert (
        approval["registry_sha256"]
        == "5a88d3e8b9f69364cf41de8655a9b7997c670b3dd660b275fab6a712a9a2dad6"
    )


@pytest.mark.parametrize("outcome", [0, 1, 2])
def test_all_gate_outcomes_retain_durable_summary_and_result(
    tmp_path: Path,
    audit: Audit,
    outcome: int,
) -> None:
    if outcome == 1:
        audit[0]["matches"][0]["artifact"]["version"] = "changed"
    elif outcome == 2:
        audit[2]["scanner_exit_codes"]["trivy"] = 1
    result = evaluate(*audit, NOW)
    write_result(tmp_path, result)
    assert json.loads((tmp_path / "result.json").read_bytes())["exit_code"] == outcome
    assert result["status"] in (tmp_path / "summary.md").read_text(encoding="utf-8")
    assert "result.json" in (tmp_path / "SHA256SUMS").read_text()


def test_cli_technical_failure_is_nonzero_and_keeps_evidence(tmp_path: Path) -> None:
    process = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/scan_image.py"),
            "--image",
            "nonexistent",
            "--approved-ref",
            "invalid",
            "--output",
            str(tmp_path),
        ],
        capture_output=True,
        check=False,
    )
    assert process.returncode == 2
    assert json.loads((tmp_path / "result.json").read_bytes())["status"] == "technical-error"
    assert (tmp_path / "summary.md").exists() and (tmp_path / "SHA256SUMS").exists()
