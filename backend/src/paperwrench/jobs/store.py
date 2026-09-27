"""Short local transactions; never performs network I/O."""

from __future__ import annotations

import json
from typing import Any

from pydantic import TypeAdapter
from pydantic import ValidationError
from sqlalchemy import func
from sqlalchemy import select
from sqlalchemy import text
from sqlalchemy import true
from sqlalchemy.orm import Session

from paperwrench.db.base import utcnow
from paperwrench.db.models import FieldKind
from paperwrench.db.models import Job
from paperwrench.db.models import JobOperation
from paperwrench.db.models import JobStatus
from paperwrench.db.models import JobTarget
from paperwrench.db.models import JobType
from paperwrench.db.models import OperationStatus
from paperwrench.db.models import PreviewDocument
from paperwrench.db.models import TargetStatus
from paperwrench.db.session import session_scope
from paperwrench.errors import NotFoundError
from paperwrench.jobs.model import CreateJob
from paperwrench.jobs.model import HistoryPage
from paperwrench.jobs.model import JobView
from paperwrench.jobs.model import OperationView
from paperwrench.jobs.model import TargetView
from paperwrench.previews.model import PAGE_SIZE
from paperwrench.previews.model import PreviewRow
from paperwrench.previews.service import PreviewService
from paperwrench.previews.service import canonical
from paperwrench.previews.service import limit
from paperwrench.previews.service import stale
from paperwrench.transformations.model import FieldValue

ACTIVE_TARGETS = (TargetStatus.PENDING, TargetStatus.READING, TargetStatus.WRITING)
UNSCOPED_OWNER = object()


def encoded(value: Any) -> str:
    return canonical(value).decode("utf-8")


def rollback_candidate(operation: JobOperation) -> bool:
    if (
        operation.status != OperationStatus.SUCCEEDED
        or operation.attempts <= 0
        or operation.before_value_json is None
        or operation.written_value_json is None
    ):
        return False
    try:
        before = _field_value.validate_json(operation.before_value_json)
        written = _field_value.validate_json(operation.written_value_json)
    except ValidationError:
        # Legacy scalar evidence cannot acquire M8 write provenance via migration.
        return False
    return encoded(before.model_dump(mode="json")) != encoded(written.model_dump(mode="json"))


_field_value: TypeAdapter[FieldValue] = TypeAdapter(FieldValue)


def create_job(request: CreateJob, owner_id: int | None = None) -> int:
    spec = request.transformation
    from paperwrench.filters.model import CustomFieldRef

    if (
        any(isinstance(o.field, CustomFieldRef) for o in spec.operations)
        and not request.acknowledge_external_race
    ):
        raise limit("Custom writes require acknowledgement of the external-writer race.")
    with session_scope() as session:
        session.execute(text("BEGIN IMMEDIATE"))
        summary = PreviewService().claim(session, request.preview_id, request, owner_id)
        job = Job(
            owner_id=owner_id,
            type=JobType.TRANSFORM,
            title="Document transformation",
            preview_id=request.preview_id,
            preview_summary_json=summary.model_dump_json(),
            source_kind=spec.targets.source,
            transformation_json=encoded([o.model_dump(mode="json") for o in spec.operations]),
            filterset_json=(
                spec.targets.query.model_dump_json() if spec.targets.source == "dataset" else None
            ),
            acknowledge_external_race=request.acknowledge_external_race,
            total_count=summary.evaluated,
        )
        session.add(job)
        session.flush()
        position = -1
        copied = 0
        while True:
            batch = session.scalars(
                select(PreviewDocument)
                .where(
                    PreviewDocument.preview_id == request.preview_id,
                    PreviewDocument.position > position,
                )
                .order_by(PreviewDocument.position)
                .limit(PAGE_SIZE)
            ).all()
            if not batch:
                break
            for staged in batch:
                row = PreviewRow.model_validate_json(staged.result_json)
                if not row.observed_revision or not row.catalog_revision:
                    raise stale("This preview predates execution preconditions; preview again.")
                session.add(
                    JobTarget(
                        job_id=job.id,
                        document_id=staged.document_id,
                        position=staged.position,
                        preview_json=staged.result_json,
                    )
                )
                for change in row.changes:
                    custom = isinstance(change.field, CustomFieldRef)
                    session.add(
                        JobOperation(
                            job_id=job.id,
                            document_id=row.document_id,
                            document_title=row.title,
                            field_kind=FieldKind.CUSTOM_FIELD if custom else FieldKind.CORE,
                            field_key=change.field.key,
                            intended_value_json=(
                                change.intended.model_dump_json()
                                if change.intended is not None
                                else None
                            ),
                        )
                    )
                copied += 1
            position = batch[-1].position
            session.flush()
            # SQLAlchemy's identity map holds weak references, not all batches.
        if copied != summary.evaluated:
            raise stale("Preview target snapshot is incomplete.")
        return job.id


def require_job(
    session: Session, job_id: int, owner_id: int | None | object = UNSCOPED_OWNER
) -> Job:
    job = session.get(Job, job_id)
    if job is None or (owner_id is not UNSCOPED_OWNER and job.owner_id != owner_id):
        raise NotFoundError("Job not found.")
    return job


def counts_for(session: Session, job_id: int) -> dict[str, int]:
    counts: dict[str, int] = {status.value: 0 for status in TargetStatus}
    for status, count in session.execute(
        select(JobTarget.status, func.count())
        .where(JobTarget.job_id == job_id)
        .group_by(JobTarget.status)
    ):
        counts[status] = count
    return counts


def finalize(session: Session, job_id: int) -> None:
    job = require_job(session, job_id)
    if (
        session.scalar(
            select(JobTarget.document_id)
            .where(JobTarget.job_id == job_id, JobTarget.status.in_(ACTIVE_TARGETS))
            .limit(1)
        )
        is not None
    ):
        return
    counts = counts_for(session, job_id)
    good = counts[TargetStatus.SUCCEEDED] + counts[TargetStatus.UNCHANGED]
    job.status = (
        JobStatus.COMPLETED
        if good == job.total_count
        else JobStatus.PARTIAL
        if good or counts[TargetStatus.AMBIGUOUS]
        else JobStatus.FAILED
    )
    job.succeeded_count = counts[TargetStatus.SUCCEEDED]
    job.skipped_count = sum(
        counts[s]
        for s in (
            TargetStatus.UNCHANGED,
            TargetStatus.CONFLICT,
            TargetStatus.PERMISSION,
            TargetStatus.MISSING,
        )
    )
    job.failed_count = counts[TargetStatus.FAILED] + counts[TargetStatus.AMBIGUOUS]
    job.finished_at = utcnow()


def _view(session: Session, job: Job) -> JobView:
    counts = counts_for(session, job.id)
    return JobView(
        id=job.id,
        type=job.type,
        rollback_of_job_id=job.rollback_of_job_id,
        rollback_job_id=session.scalar(select(Job.id).where(Job.rollback_of_job_id == job.id)),
        title=job.title,
        status=job.status,
        total=job.total_count,
        processed=job.total_count - sum(counts[s] for s in ACTIVE_TARGETS),
        counts=counts,
        source_kind=job.source_kind,
        dataset_query=json.loads(job.filterset_json) if job.filterset_json else None,
        operations=json.loads(job.transformation_json) if job.transformation_json else [],
        preview=json.loads(job.preview_summary_json) if job.preview_summary_json else None,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        resumable=job.status == JobStatus.INTERRUPTED and counts[TargetStatus.PENDING] > 0,
    )


def job_view(job_id: int, owner_id: int | None | object = UNSCOPED_OWNER) -> JobView:
    with session_scope() as session:
        return _view(session, require_job(session, job_id, owner_id))


def _pagination(page: int, page_size: int, total: int) -> dict[str, int]:
    if page < 1 or page_size not in (25, 50, 100, 250):
        raise limit("History requires positive pages and page_size 25, 50, 100 or 250.")
    return {
        "page": page,
        "page_size": page_size,
        "total": total,
        "page_count": (total + page_size - 1) // page_size,
    }


def job_page(
    page: int, page_size: int, owner_id: int | None | object = UNSCOPED_OWNER
) -> HistoryPage[JobView]:
    with session_scope() as session:
        condition = true() if owner_id is UNSCOPED_OWNER else Job.owner_id == owner_id
        total = session.scalar(select(func.count()).select_from(Job).where(condition)) or 0
        pagination = _pagination(page, page_size, total)
        jobs = session.scalars(
            select(Job)
            .where(condition)
            .order_by(Job.id.desc())
            .limit(page_size)
            .offset((page - 1) * page_size)
        )
        return HistoryPage(items=[_view(session, job) for job in jobs], **pagination)


def target_page(
    job_id: int,
    page: int,
    page_size: int,
    status: TargetStatus | None,
    owner_id: int | None | object = UNSCOPED_OWNER,
) -> HistoryPage[TargetView]:
    with session_scope() as session:
        require_job(session, job_id, owner_id)
        condition = JobTarget.job_id == job_id
        if status is not None:
            condition = condition & (JobTarget.status == status)
        total = session.scalar(select(func.count()).select_from(JobTarget).where(condition)) or 0
        pagination = _pagination(page, page_size, total)
        targets = session.scalars(
            select(JobTarget)
            .where(condition)
            .order_by(JobTarget.position)
            .limit(page_size)
            .offset((page - 1) * page_size)
        )
        return HistoryPage(
            items=[
                TargetView(
                    document_id=t.document_id,
                    position=t.position,
                    title=json.loads(t.preview_json).get("title") if t.preview_json else None,
                    excluded_operations=(
                        json.loads(t.preview_json).get("excluded_operations", {})
                        if t.preview_json else {}
                    ),
                    status=t.status,
                    error=t.error,
                    http_status=t.http_status,
                    attempts=t.attempts,
                    started_at=t.started_at,
                    finished_at=t.finished_at,
                )
                for t in targets
            ],
            **pagination,
        )


def operation_page(
    job_id: int,
    page: int,
    page_size: int,
    document_id: int | None,
    owner_id: int | None | object = UNSCOPED_OWNER,
) -> HistoryPage[OperationView]:
    with session_scope() as session:
        require_job(session, job_id, owner_id)
        condition = JobOperation.job_id == job_id
        if document_id is not None:
            condition = condition & (JobOperation.document_id == document_id)
        total = session.scalar(select(func.count()).select_from(JobOperation).where(condition)) or 0
        pagination = _pagination(page, page_size, total)
        operations = session.scalars(
            select(JobOperation)
            .where(condition)
            .order_by(JobOperation.id)
            .limit(page_size)
            .offset((page - 1) * page_size)
        )
        return HistoryPage(
            items=[
                OperationView(
                    id=o.id,
                    rollback_of_operation_id=o.rollback_of_operation_id,
                    document_id=o.document_id,
                    field_kind=o.field_kind,
                    field_key=o.field_key,
                    status=o.status,
                    before=json.loads(o.before_value_json) if o.before_value_json else None,
                    intended=json.loads(o.intended_value_json) if o.intended_value_json else None,
                    written=json.loads(o.written_value_json) if o.written_value_json else None,
                    rollback_candidate=rollback_candidate(o),
                    error=o.error,
                    http_status=o.http_status,
                    attempts=o.attempts,
                    started_at=o.started_at,
                    finished_at=o.finished_at,
                )
                for o in operations
            ],
            **pagination,
        )
