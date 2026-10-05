"""Evaluate unfiltered image reports against previously approved, bounded decisions."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import asdict
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from typing import Any

RANK = {"Unknown": 0, "Negligible": 1, "Low": 2, "Medium": 3, "High": 4, "Critical": 5}
DISPOSITIONS = {"code-not-shipped", "not-applicable-architecture", "mitigated", "accepted-risk"}


class AuditError(ValueError):
    """A technical/policy failure, never a clean scan."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AuditError(message)


def timestamp(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError) as exc:
        raise AuditError(f"Invalid UTC timestamp: {value!r}") from exc
    require(result.tzinfo is not None, "Timestamp must include a timezone")
    return result.astimezone(UTC)


def fresh(value: str, now: datetime) -> None:
    age = now - timestamp(value)
    require(-timedelta(minutes=5) <= age <= timedelta(hours=24), "DB is stale or in the future")


@dataclass(frozen=True)
class Finding:
    scanner: str
    advisory: str
    package: str
    version: str
    kind: str
    severity: str
    paths: tuple[str, ...]
    fixes: tuple[str, ...]


def severity(value: str) -> str:
    result = value.title()
    return result if result in RANK else "Unknown"


def normalize(grype: dict[str, Any], trivy: dict[str, Any]) -> list[Finding]:
    require(isinstance(grype.get("matches"), list), "Missing Grype matches")
    require(not grype.get("ignoredMatches"), "Grype ignored findings are forbidden")
    require(trivy.get("SchemaVersion") == 2, "Unsupported Trivy report schema")
    require(trivy.get("ArtifactType") == "container_image", "Trivy did not scan an image")
    require(isinstance(trivy.get("Results"), list), "Missing Trivy results")
    findings = []
    for match in grype["matches"]:
        artifact, vuln = match["artifact"], match["vulnerability"]
        findings.append(
            Finding(
                "grype",
                vuln["id"],
                artifact["name"],
                artifact["version"],
                artifact["type"],
                severity(vuln["severity"]),
                tuple(sorted({loc["path"] for loc in artifact.get("locations", [])})),
                tuple(vuln.get("fix", {}).get("versions", [])),
            )
        )
    for result in trivy["Results"]:
        require(not result.get("Misconfigurations"), "Unexpected Trivy scan scope")
        require(not result.get("ModifiedFindings"), "Trivy filtered findings are forbidden")
        for vuln in result.get("Vulnerabilities", []):
            findings.append(
                Finding(
                    "trivy",
                    vuln["VulnerabilityID"],
                    vuln["PkgName"],
                    vuln["InstalledVersion"],
                    result["Type"],
                    severity(vuln["Severity"]),
                    (vuln["PkgPath"],) if vuln.get("PkgPath") else (),
                    (vuln["FixedVersion"],) if vuln.get("FixedVersion") else (),
                )
            )
    for finding in findings:
        require(
            all(
                isinstance(v, str) and v
                for v in (
                    finding.advisory,
                    finding.package,
                    finding.version,
                    finding.kind,
                )
            ),
            "Incomplete finding identity",
        )
    return findings


def validate_reports(
    grype: dict[str, Any],
    trivy: dict[str, Any],
    metadata: dict[str, Any],
    now: datetime,
) -> None:
    require(metadata["scanner_exit_codes"] == {"grype": 0, "trivy": 0}, "Scanner execution failed")
    image = metadata["image"]
    image_id = image["Id"]
    require(re.fullmatch(r"sha256:[a-f0-9]{64}", image_id) is not None, "Invalid image ID")
    revision = image["Config"]["Labels"].get("org.opencontainers.image.revision", "")
    require(re.fullmatch(r"[a-f0-9]{40}", revision) is not None, "Missing immutable OCI revision")
    require(revision == metadata["source_commit"], "Image/source revision mismatch")
    target = grype["source"]["target"]
    require(grype["source"]["type"] == "image", "Grype did not scan an image")
    require(target["userInput"] in (image_id, "docker:" + image_id), "Grype target mismatch")
    # Docker's legacy export may rewrite the config digest. Verify the actual
    # requested ID plus exported config revision and layer diff IDs instead.
    config = json.loads(base64.b64decode(target["config"]))
    require(
        config["config"]["Labels"].get("org.opencontainers.image.revision") == revision,
        "Grype exported revision mismatch",
    )
    require(config["rootfs"]["diff_ids"] == image["RootFS"]["Layers"], "Grype layer mismatch")
    require(trivy["ArtifactName"] == image_id, "Trivy target mismatch")
    require(trivy["Metadata"]["ImageID"] == image_id, "Trivy image ID mismatch")
    require(trivy["Metadata"]["DiffIDs"] == image["RootFS"]["Layers"], "Trivy layer mismatch")
    require(
        config["architecture"]
        == image["Architecture"]
        == trivy["Metadata"]["ImageConfig"]["architecture"],
        "Scanner architecture mismatch",
    )
    require(
        config["os"] == image["Os"] == trivy["Metadata"]["ImageConfig"]["os"], "Scanner OS mismatch"
    )
    db = grype["descriptor"]["db"]["status"]
    require(
        db["valid"] is True and re.fullmatch(r"v6\.\d+\.\d+", db["schemaVersion"]) is not None,
        "Invalid/unsupported Grype DB",
    )
    fresh(db["built"], now)
    fresh(metadata["grype_db"]["built"], now)
    require(db["built"] == metadata["grype_db"]["built"], "Grype DB changed during scan")
    require(metadata["trivy_db"]["Version"] == 2, "Unsupported Trivy DB")
    fresh(metadata["trivy_db"]["UpdatedAt"], now)
    require(
        metadata["tools"] == {"grype": "0.120.0", "trivy": "0.75.0"}, "Unsupported scanner versions"
    )


def scope_matches(metadata: dict[str, Any], approval: dict[str, Any]) -> bool:
    image, probe, container = metadata["image"], metadata["probe"], metadata["container"]
    flags = container["HostConfig"]
    return bool(
        image["Os"] == "linux"
        and image["Architecture"] == "amd64"
        and probe["architecture"] == "x86_64"
        and probe["uid"] == 10001
        and probe["debian"]["ID"] == "debian"
        and probe["debian"]["VERSION_ID"] == "13"
        and metadata["runtime_tree_sha256"] == approval["runtime_tree_sha256"]
        and flags["ReadonlyRootfs"]
        and flags["CapDrop"] == ["ALL"]
        and flags["SecurityOpt"] == ["no-new-privileges"]
        and "/tmp" in flags["Tmpfs"]
        and not [m for m in container["Mounts"] if m["Destination"] not in ("/data", "/tmp")]
        and not [
            line
            for line in probe["fstab"].splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        and not probe["privileged_files"]
        and not probe["node_modules"]
        and probe["usrmerge"] == {"/bin": "/usr/bin", "/sbin": "/usr/sbin"}
        and not any(probe["installer_imports"].values())
    )


def absence_checks(probe: dict[str, Any]) -> None:
    require(
        not probe["perl_modules"]["Pod::Text"]["importable"]
        and not probe["perl_modules"]["Archive::Tar"]["importable"],
        "Perl modules changed",
    )
    require(probe["perl_modules"]["File::Temp"]["importable"], "Perl runtime inventory changed")
    for name in (
        "getfacl",
        "setfacl",
        "chacl",
        "getfattr",
        "setfattr",
        "bzip2recover",
        "nscd",
        "systemd",
        "systemd-homed",
        "systemd-oomd",
        "systemd-journald",
        "newuidmap",
        "apt-key",
    ):
        require(probe["tools"][name] is None, f"Previously absent tool now present: {name}")
    symbols = probe["sqlite_symbols"]
    require(
        not symbols["sqlite3changeset_apply_v3"] and not symbols["sqlite3_zipfile_init"],
        "SQLite affected symbol scope changed",
    )
    require(
        symbols["sqlite3changeset_concat"] and "ENABLE_SESSION" in probe["sqlite_compile_options"],
        "SQLite inventory scope changed",
    )
    require(
        "no such function" in probe["sqlite_zipfile_probe_error"], "SQLite zipfile scope changed"
    )


def decisions(
    registry: dict[str, Any],
    now: datetime,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    require(
        registry.get("schema_version") == 1 and isinstance(registry.get("records"), list),
        "Invalid decision registry",
    )
    aliases: dict[str, dict[str, Any]] = {}
    errors = []
    for record in registry["records"]:
        require(record["id"] in record["aliases"], "Decision lacks its identity")
        require(record["disposition"] in DISPOSITIONS, "Invalid decision disposition")
        require(record["maximum_scanner_severity"] in RANK, "Invalid decision severity")
        require(
            bool(
                record["owner"]
                and record["assessment"]
                and record["sources"]
                and record["review_triggers"]
                and record["followup_issue"]
            ),
            "Incomplete decision ownership/evidence",
        )
        require(
            isinstance(record["occurrences"], list) and bool(record["occurrences"]),
            "Decision must have exact occurrences",
        )
        if timestamp(record["expires_at"]) <= now:
            errors.append("Expired decision: " + record["id"])
        for alias in record["aliases"]:
            require(alias not in aliases, f"Ambiguous/duplicate advisory alias: {alias}")
            aliases[alias] = record
    return aliases, errors


def evaluate(
    grype: dict[str, Any],
    trivy: dict[str, Any],
    metadata: dict[str, Any],
    registry: dict[str, Any],
    approval: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    """Return exit 0 (pass), 1 (findings), or 2 (technical/policy error)."""
    try:
        validate_reports(grype, trivy, metadata, now)
        findings = normalize(grype, trivy)
        aliases, errors = decisions(registry, now)
        valid_scope = scope_matches(metadata, approval)
        if valid_scope:
            absence_checks(metadata["probe"])
        else:
            errors.append(
                "Approved runtime/deployment scope cannot be verified; revalidation required"
            )
        maximum: dict[str, str] = {}
        for finding in findings:
            record = aliases.get(finding.advisory)
            identity = record["id"] if record else finding.advisory
            previous = maximum.get(identity, "Unknown")
            maximum[identity] = max(previous, finding.severity, key=RANK.__getitem__)
        rows = []
        blocked = 0
        for finding in findings:
            record = aliases.get(finding.advisory)
            identity = record["id"] if record else finding.advisory
            matched = False
            if record and valid_scope and timestamp(record["expires_at"]) > now:
                matched = RANK[maximum[identity]] <= RANK[
                    record["maximum_scanner_severity"]
                ] and any(
                    v["scanner"] == finding.scanner
                    and v["package"] == finding.package
                    and v["version"] == finding.version
                    and v["type"] == finding.kind
                    and (finding.severity != "Unknown" or v["severity"] == "Unknown")
                    and tuple(sorted(v["scanner_paths"])) == finding.paths
                    for v in record["occurrences"]
                )
            reason = "approved exception" if matched else "unreviewed finding"
            blocking = not matched and (
                maximum[identity] in ("Critical", "High", "Unknown")
                or finding.severity == "Unknown"
            )
            if not matched and not blocking:
                first_seen = approval.get("first_seen", {}).get(identity)
                if first_seen:
                    since = timestamp(first_seen)
                    require(since <= now, "First-seen date is in the future")
                    blocking = now - since >= timedelta(days=30)
                    reason = "review overdue" if blocking else "review due within 30 days"
                else:
                    blocking = True
                    reason = "initial classification required (no approved first-seen date)"
            if record and not valid_scope:
                reason = "reviewed runtime/deployment scope changed"
            if (
                record
                and finding.fixes
                and any(
                    v["scanner"] == finding.scanner
                    and v["package"] == finding.package
                    and not (
                        v["scanner_fix"].get("versions") or v["scanner_fix"].get("fixed_version")
                    )
                    for v in record["occurrences"]
                )
            ):
                blocking = True
                matched = False
                reason = "new reported correction requires review"
            blocked += int(blocking)
            row = asdict(finding)
            row.update(
                identity=identity,
                maximum_severity=maximum[identity],
                exception=matched,
                disposition=record["disposition"] if record and matched else None,
                expires_at=record["expires_at"] if record and matched else None,
                followup_issue=record["followup_issue"] if record else None,
                blocking=blocking,
                reason=reason,
            )
            rows.append(row)
        return {
            "exit_code": 2 if errors else 1 if blocked else 0,
            "status": "policy-error" if errors else "findings" if blocked else "pass",
            "errors": errors,
            "scope_valid": valid_scope,
            "blocked": blocked,
            "occurrences": len(rows),
            "identities": len(maximum),
            "findings": rows,
        }
    except (AuditError, KeyError, TypeError, ValueError) as exc:
        return {"exit_code": 2, "status": "technical-error", "errors": [str(exc)], "findings": []}


def registry_digest(registry_bytes: bytes) -> str:
    return hashlib.sha256(registry_bytes).hexdigest()
