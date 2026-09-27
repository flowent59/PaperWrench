"""Rollback planning and atomic adoption; execution belongs to the M8 engine."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy import text
from sqlalchemy.orm import Session

from paperwrench.db.base import utcnow
from paperwrench.db.models import Job
from paperwrench.db.models import JobOperation
from paperwrench.db.models import JobStatus
from paperwrench.db.models import JobTarget
from paperwrench.db.models import JobType
from paperwrench.db.models import OperationStatus
from paperwrench.db.models import Preview
from paperwrench.db.models import PreviewDocument
from paperwrench.db.models import TargetStatus
from paperwrench.db.session import session_scope
from paperwrench.errors import PaperlessForbiddenError
from paperwrench.filters.model import CustomFieldRef
from paperwrench.jobs.model import CreateRollback
from paperwrench.jobs.store import UNSCOPED_OWNER
from paperwrench.jobs.store import encoded
from paperwrench.jobs.store import require_job
from paperwrench.jobs.store import rollback_candidate
from paperwrench.paperless import PaperlessClient
from paperwrench.paperless.errors import PaperlessNotFoundError
from paperwrench.paperless.models import CustomField
from paperwrench.previews.model import PAGE_SIZE
from paperwrench.previews.model import PreviewRow
from paperwrench.previews.model import PreviewSummary
from paperwrench.previews.service import _row
from paperwrench.previews.service import limit
from paperwrench.previews.service import stale
from paperwrench.transformations.model import ResultStatus
from paperwrench.transformations.model import Transformation
from paperwrench.transformations.model import TransformationIssue


def require_original(
    session: Session,
    job_id: int,
    owner_id: int | None | object = UNSCOPED_OWNER,
) -> Job:
    job = require_job(session, job_id, owner_id)
    if job.type != JobType.TRANSFORM or not JobStatus(job.status).is_terminal:
        raise stale("Rollback requires a terminal transformation Job.")
    if session.scalar(select(Job.id).where(Job.rollback_of_job_id == job_id)) is not None:
        raise stale("A rollback Job already exists; inspect its History or resume unsent work.")
    return job


async def preview_row(
    job_id: int,
    document_id: int,
    client: PaperlessClient,
    fields: dict[int, CustomField],
    owner_id: int | None = None,
) -> PreviewRow:
    operations: list[dict[str, Any]] = []
    expected: list[Any] = []
    links: list[int] = []
    excluded: dict[str, str] = {}
    with session_scope() as session:
        require_original(session, job_id, owner_id)
        target = session.get(JobTarget, (job_id, document_id))
        assert target is not None
        original = (
            PreviewRow.model_validate_json(target.preview_json)
            if target.preview_json
            else PreviewRow(document_id=document_id, changes=[], status=ResultStatus.UNCHANGED)
        )
        refs = {change.field.key: change.field for change in original.changes}
        for operation in session.scalars(
            select(JobOperation)
            .where(JobOperation.job_id == job_id, JobOperation.document_id == document_id)
            .order_by(JobOperation.id)
        ):
            if not rollback_candidate(operation) or operation.field_key not in refs:
                excluded[operation.field_key] = (
                    "MANUAL_REVIEW"
                    if operation.status == OperationStatus.AMBIGUOUS
                    else "NO_PROVEN_WRITE"
                )
                continue
            assert operation.before_value_json and operation.written_value_json
            before = json.loads(operation.before_value_json)
            field = refs[operation.field_key]
            restore: dict[str, Any] = {"field": field.model_dump(mode="json")}
            if before["kind"] == "present":
                restore.update(operation="set", value=before["raw"])
            else:
                restore.update(operation="clear")
                if isinstance(field, CustomFieldRef):
                    restore["state"] = before["kind"]
            operations.append(restore)
            expected.append(json.loads(operation.written_value_json))
            links.append(operation.id)
    row = PreviewRow(
        document_id=document_id,
        title=original.title,
        changes=[],
        status=ResultStatus.UNCHANGED,
        rollback_operation_ids=links,
        excluded_operations=excluded,
    )
    if not operations:
        if "MANUAL_REVIEW" in excluded.values():
            row.status = ResultStatus.ERROR
            row.issue = TransformationIssue(
                code="MANUAL_REVIEW",
                message="Original write outcome is unknown; automatic rollback is forbidden.",
            )
        return row
    spec = Transformation.model_validate(
        {
            "targets": {"source": "ids", "document_ids": [document_id]},
            "operations": operations,
        }
    )
    row.rollback_spec = spec
    try:
        document = await client.get_document(document_id)
        if document.id != document_id:
            raise stale("Paperless returned a different document ID.")
        if document.deleted_at is not None:
            raise PaperlessNotFoundError("Document is deleted or in trash.")
        row = _row(document, spec, fields).model_copy(
            update={
                "rollback_spec": spec,
                "rollback_operation_ids": links,
                "excluded_operations": excluded,
            }
        )
        if row.status != ResultStatus.ERROR and any(
            encoded(change.before.model_dump(mode="json")) != encoded(written)
            for change, written in zip(row.changes, expected, strict=True)
        ):
            row.status = ResultStatus.ERROR
            row.issue = TransformationIssue(
                code="ROLLBACK_CONFLICT",
                message=(
                    "A candidate differs from the original written value; "
                    "no field of this document will be restored."
                ),
            )
    except (PaperlessNotFoundError, PaperlessForbiddenError) as exc:
        row.status = ResultStatus.ERROR
        row.issue = TransformationIssue(
            code="DOCUMENT_UNAVAILABLE"
            if isinstance(exc, PaperlessNotFoundError)
            else "DOCUMENT_FORBIDDEN",
            message="Document is missing, invisible or not editable.",
        )
    return row


def create_rollback(
    job_id: int, request: CreateRollback, owner_id: int | None = None
) -> int:
    with session_scope() as session:
        session.execute(text("BEGIN IMMEDIATE"))
        require_original(session, job_id, owner_id)
        preview = session.get(Preview, request.preview_id)
        if (
            preview is None
            or preview.owner_id != owner_id
            or not preview.ready
            or preview.confirmed
            or preview.expires_at <= utcnow()
        ):
            raise stale()
        summary = PreviewSummary.model_validate_json(preview.summary_json)
        if (
            summary.rollback_of_job_id != job_id
            or not summary.changed
            or request.target_fingerprint != summary.target_fingerprint
            or request.result_fingerprint != summary.result_fingerprint
            or request.version != summary.version
            or not hmac.compare_digest(
                preview.token_hash, hashlib.sha256(request.preview_token.encode()).hexdigest()
            )
        ):
            raise stale("Confirmation does not match a rollback preview with eligible changes.")
        preview.confirmed = True
        job = Job(
            owner_id=owner_id,
            type=JobType.ROLLBACK,
            title=f"Rollback of Job #{job_id}",
            rollback_of_job_id=job_id,
            preview_id=preview.id,
            preview_summary_json=summary.model_copy(update={"confirmed": True}).model_dump_json(),
            source_kind="rollback",
            total_count=summary.evaluated,
            acknowledge_external_race=request.acknowledge_external_race,
        )
        session.add(job)
        session.flush()
        position = -1
        copied = 0
        while True:
            batch = session.scalars(
                select(PreviewDocument)
                .where(
                    PreviewDocument.preview_id == preview.id, PreviewDocument.position > position
                )
                .order_by(PreviewDocument.position)
                .limit(PAGE_SIZE)
            ).all()
            if not batch:
                break
            for staged in batch:
                copied += 1
                row = PreviewRow.model_validate_json(staged.result_json)
                if (
                    row.rollback_spec
                    and any(
                        isinstance(o.field, CustomFieldRef) for o in row.rollback_spec.operations
                    )
                    and not request.acknowledge_external_race
                ):
                    raise limit(
                        "Custom writes require acknowledgement of the external-writer race."
                    )
                status = TargetStatus.PENDING
                if row.status == ResultStatus.UNCHANGED:
                    status = TargetStatus.UNCHANGED
                elif row.status == ResultStatus.ERROR:
                    code = row.issue.code if row.issue else "ROLLBACK_INVALID"
                    status = (
                        TargetStatus.MISSING
                        if code == "DOCUMENT_UNAVAILABLE"
                        else TargetStatus.PERMISSION
                        if code in ("DOCUMENT_FORBIDDEN", "DOCUMENT_NOT_EDITABLE")
                        else TargetStatus.FAILED
                        if code == "MANUAL_REVIEW"
                        else TargetStatus.CONFLICT
                    )
                session.add(
                    JobTarget(
                        job_id=job.id,
                        document_id=row.document_id,
                        position=staged.position,
                        preview_json=staged.result_json,
                        status=status,
                        error=row.issue.code if row.issue else None,
                        finished_at=utcnow() if status != TargetStatus.PENDING else None,
                    )
                )
                for original_id in row.rollback_operation_ids:
                    operation = session.get(JobOperation, original_id)
                    assert operation is not None
                    if not rollback_candidate(operation) or operation.job_id != job_id:
                        raise stale("Original operation no longer has certain provenance.")
                    session.add(
                        JobOperation(
                            job_id=job.id,
                            document_id=row.document_id,
                            document_title=row.title,
                            field_kind=operation.field_kind,
                            field_key=operation.field_key,
                            intended_value_json=operation.before_value_json,
                            rollback_of_operation_id=operation.id,
                            status=OperationStatus.PENDING
                            if status == TargetStatus.PENDING
                            else {
                                TargetStatus.UNCHANGED: OperationStatus.SKIPPED_UNCHANGED,
                                TargetStatus.MISSING: OperationStatus.SKIPPED_MISSING,
                                TargetStatus.PERMISSION: OperationStatus.SKIPPED_PERMISSION,
                                TargetStatus.CONFLICT: OperationStatus.SKIPPED_CONFLICT,
                                TargetStatus.FAILED: OperationStatus.FAILED,
                            }[status],
                            error=row.issue.code if row.issue else None,
                            finished_at=utcnow() if status != TargetStatus.PENDING else None,
                        )
                    )
            position = batch[-1].position
            session.flush()
        if copied != summary.evaluated:
            raise stale("Rollback preview target snapshot is incomplete.")
        return job.id
