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

import scan_image
from image_security import AuditError
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


@pytest.mark.parametrize("change", ["owner", "occurrence", "expiry", "alias", "evidence"])
def test_malformed_decision_is_a_policy_failure(audit: Audit, change: str) -> None:
    record = audit[3]["records"][0]
    if change == "owner":
        record["owner"] = ""
    elif change == "occurrence":
        del record["occurrences"][0]["scanner_paths"]
    elif change == "expiry":
        record["expires_at"] = "not-a-date"
    elif change == "alias":
        record["aliases"].append(audit[3]["records"][1]["id"])
    else:
        record["evidence"] = []
    assert evaluate(*audit, NOW)["exit_code"] == 2


def test_unrecognized_severity_cannot_reuse_a_known_unknown_decision(audit: Audit) -> None:
    match = audit[0]["matches"][0]
    match["vulnerability"]["severity"] = "Unknown"
    record = next(
        record
        for record in audit[3]["records"]
        if match["vulnerability"]["id"] in record["aliases"]
    )
    for occurrence in record["occurrences"]:
        if occurrence["scanner"] == "grype":
            occurrence["severity"] = "Unknown"
    assert evaluate(*audit, NOW)["findings"][0]["exception"]
    match["vulnerability"]["severity"] = "unexpected-new-enumeration"
    result = evaluate(*audit, NOW)
    row = next(
        row
        for row in result["findings"]
        if row["reported_severity"] == "unexpected-new-enumeration"
    )
    assert row["blocking"] and not row["exception"] and result["exit_code"] == 1


@pytest.mark.parametrize("change", ["native", "packages", "distribution"])
def test_missing_inventory_and_distribution_mismatch_fail(audit: Audit, change: str) -> None:
    if change == "native":
        audit[2]["probe"]["packages_tsv"] = ""
    elif change == "packages":
        for result in audit[1]["Results"]:
            result.pop("Packages", None)
    else:
        audit[1]["Metadata"]["OS"]["Family"] = "unexpected"
    assert evaluate(*audit, NOW)["exit_code"] == 2


def test_unapproved_registry_hash_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    original = scan_image.git_file

    def altered(ref: str, path: str) -> bytes:
        raw = original(ref, path)
        if path == ".security/reviewed-runtime.json":
            policy = json.loads(raw)
            policy["registry_sha256"] = "unapproved"
            return json.dumps(policy).encode()
        return raw

    monkeypatch.setattr(scan_image, "git_file", altered)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip()
    # The commit is used only to exercise loading an already present approval file.
    with pytest.raises(AuditError, match="Unapproved registry"):
        approved_policy(head)


def test_tampered_release_binary_is_rejected_before_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def corrupt_download(url: str, path: Path) -> None:
        path.write_bytes(b"not the upstream binary")

    monkeypatch.setattr(scan_image, "download", corrupt_download)
    with pytest.raises(AuditError, match="release checksum"):
        scan_image.install_tools(tmp_path, tmp_path)


def test_unfixed_trivy_high_is_not_exempt(audit: Audit) -> None:
    vulnerabilities = audit[1]["Results"][0]["Vulnerabilities"]
    finding = copy.deepcopy(vulnerabilities[0])
    finding.update(VulnerabilityID="CVE-2099-9999", Severity="HIGH", FixedVersion="")
    vulnerabilities.append(finding)
    result = evaluate(*audit, NOW)
    row = next(row for row in result["findings"] if row["identity"] == "CVE-2099-9999")
    assert result["exit_code"] == 1 and row["blocking"] and not row["fixes"]


def test_new_fix_on_another_branch_still_requires_review(audit: Audit) -> None:
    match = next(
        match for match in audit[0]["matches"] if match["vulnerability"]["fix"]["versions"]
    )
    match["vulnerability"]["fix"]["versions"].append("new-supported-fix")
    result = evaluate(*audit, NOW)
    rows = [row for row in result["findings"] if "new-supported-fix" in row["fixes"]]
    assert result["exit_code"] == 1 and rows
    assert all(
        row["blocking"] and row["reason"] == "new reported correction requires review"
        for row in rows
    )


@pytest.mark.parametrize("outcome", [0, 1, 2])
def test_runner_propagates_gate_exits_and_keeps_reports(
    audit: Audit,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outcome: int,
) -> None:
    if outcome == 1:
        audit[0]["matches"][0]["artifact"]["version"] = "changed"
    elif outcome == 2:
        audit[3]["records"][0]["expires_at"] = NOW.isoformat()

    def acquired_reports(args: Any, output: Path) -> dict[str, Any]:
        # Replay the retained real reports through the real evaluator. Network
        # acquisition is exercised by CI's Docker/published scans, not this fixture.
        scan_image.save(output / "grype.json", audit[0])
        scan_image.save(output / "trivy.json", audit[1])
        scan_image.save(output / "metadata.json", audit[2])
        return evaluate(*audit, NOW)

    monkeypatch.setattr(scan_image, "scan", acquired_reports)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "scan_image",
            "--image",
            "fixture",
            "--approved-ref",
            BOOTSTRAP,
            "--output",
            str(tmp_path),
        ],
    )
    assert scan_image.main() == outcome
    assert json.loads((tmp_path / "result.json").read_bytes())["exit_code"] == outcome
    assert (tmp_path / "grype.json").exists() and (tmp_path / "trivy.json").exists()
    assert (tmp_path / "summary.md").exists() and (tmp_path / "SHA256SUMS").exists()
