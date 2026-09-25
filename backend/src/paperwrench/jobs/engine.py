"""Fixed-size in-process worker pool. SQLite is the queue and the evidence."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy import text
from sqlalchemy import update
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from paperwrench.config import Settings
from paperwrench.db.base import utcnow
from paperwrench.db.lock import STALE_AFTER
from paperwrench.db.models import Job
from paperwrench.db.models import JobOperation
from paperwrench.db.models import JobStatus
from paperwrench.db.models import JobTarget
from paperwrench.db.models import JobType
from paperwrench.db.models import OperationStatus
from paperwrench.db.models import RuntimeLock
from paperwrench.db.models import TargetStatus
from paperwrench.db.session import session_scope
from paperwrench.errors import ErrorCode
from paperwrench.errors import PaperlessForbiddenError
from paperwrench.errors import PaperWrenchError
from paperwrench.filters.model import CustomFieldRef
from paperwrench.inspector import catalog_revision
from paperwrench.jobs.store import ACTIVE_TARGETS
from paperwrench.jobs.store import encoded
from paperwrench.jobs.store import finalize
from paperwrench.jobs.store import require_job
from paperwrench.logging import get_logger
from paperwrench.paperless import MetadataRegistry
from paperwrench.paperless import PaperlessClient
from paperwrench.paperless.errors import PaperlessConflictError
from paperwrench.paperless.errors import PaperlessNotFoundError
from paperwrench.paperless.models import Document
from paperwrench.paperless.models import MetadataKind
from paperwrench.paperless.mutations import MutationPlan
from paperwrench.paperless.mutations import revision
from paperwrench.previews.model import PreviewRow
from paperwrench.transformations import evaluate
from paperwrench.transformations.model import FieldValue
from paperwrench.transformations.model import ProposedChange
from paperwrench.transformations.model import ResultStatus
from paperwrench.transformations.model import Transformation

logger = get_logger(__name__)

OUTCOMES = {
    TargetStatus.SUCCEEDED: OperationStatus.SUCCEEDED,
    TargetStatus.UNCHANGED: OperationStatus.SKIPPED_UNCHANGED,
    TargetStatus.CONFLICT: OperationStatus.SKIPPED_CONFLICT,
    TargetStatus.PERMISSION: OperationStatus.SKIPPED_PERMISSION,
    TargetStatus.MISSING: OperationStatus.SKIPPED_MISSING,
    TargetStatus.FAILED: OperationStatus.FAILED,
    TargetStatus.AMBIGUOUS: OperationStatus.AMBIGUOUS,
}


class OwnershipLost(Exception):
    pass


def equal(left: FieldValue | None, right: FieldValue | None) -> bool:
    # Canonical JSON preserves false/0 and exact monetary strings.
    return encoded(left.model_dump(mode="json") if left else None) == encoded(
        right.model_dump(mode="json") if right else None
    )


def recover() -> None:
    """Boot never writes upstream. A durable send marker is never replayed."""
    with session_scope() as session:
        uncertain = select(JobTarget.document_id).where(
            JobTarget.job_id == JobOperation.job_id, JobTarget.status == TargetStatus.WRITING
        )
        session.execute(
            update(JobOperation)
            .where(
                JobOperation.document_id.in_(uncertain),
                JobOperation.status == OperationStatus.PENDING,
            )
            .values(
                status=OperationStatus.AMBIGUOUS, error="PROCESS_INTERRUPTED", finished_at=utcnow()
            ),
            execution_options={"synchronize_session": False},
        )
        session.execute(
            update(JobTarget)
            .where(JobTarget.status == TargetStatus.WRITING)
            .values(
                status=TargetStatus.AMBIGUOUS,
                error="PROCESS_INTERRUPTED",
                finished_at=utcnow(),
                execution_owner=None,
            )
        )
        session.execute(
            update(JobTarget)
            .where(JobTarget.status == TargetStatus.READING)
            .values(
                status=TargetStatus.PENDING,
                execution_owner=None,
            )
        )
        session.execute(
            update(Job)
            .where(Job.status.in_((JobStatus.PENDING, JobStatus.RUNNING)))
            .values(status=JobStatus.INTERRUPTED)
        )
        # Clear attempt ownership for the recovered Job, including completed
        # targets. An old scheduler may still wake after the new one resumes.
        session.execute(
            update(JobTarget)
            .where(
                JobTarget.job_id.in_(select(Job.id).where(Job.status == JobStatus.INTERRUPTED)),
                JobTarget.execution_owner.is_not(None),
            )
            .values(execution_owner=None),
            execution_options={"synchronize_session": False},
        )
        last = 0
        while True:
            ids = session.scalars(
                select(Job.id)
                .where(Job.id > last, Job.status == JobStatus.INTERRUPTED)
                .order_by(Job.id)
                .limit(100)
            ).all()
            if not ids:
                break
            for job_id in ids:
                finalize(session, job_id)
            last = ids[-1]


class JobEngine:
    def __init__(
        self,
        client: PaperlessClient,
        registry: MetadataRegistry,
        settings: Settings,
        instance_id: str,
    ) -> None:
        self.client = client
        self.registry = registry
        self.settings = settings
        self.instance_id = instance_id
        self.stopped = False
        self.wake = asyncio.Event()
        self.workers: list[asyncio.Task[None]] = []

    def start(self) -> None:
        self.workers = [
            asyncio.create_task(self._worker()) for _ in range(self.settings.max_concurrency)
        ]

    def stop_scheduling(self) -> None:
        self.stopped = True
        self.wake.set()
        with session_scope() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            lock = session.get(RuntimeLock, 1)
            condition: ColumnElement[bool] = Job.status.in_((JobStatus.PENDING, JobStatus.RUNNING))
            if lock is None or lock.instance_id != self.instance_id:
                condition = condition & Job.id.in_(
                    select(JobTarget.job_id).where(
                        JobTarget.execution_owner == self.instance_id,
                    )
                )
            session.execute(update(Job).where(condition).values(status=JobStatus.INTERRUPTED))

    def ensure_owner(self, session: Session) -> None:
        lock = session.get(RuntimeLock, 1)
        if (
            self.stopped
            or lock is None
            or lock.instance_id != self.instance_id
            or utcnow() - lock.heartbeat_at > STALE_AFTER
        ):
            self.stopped = True
            raise OwnershipLost

    def check_available(self) -> None:
        try:
            with session_scope() as session:
                self.ensure_owner(session)
        except OwnershipLost as exc:
            raise PaperWrenchError(
                "Runtime ownership is unavailable; restart before applying.",
                status_code=503,
                code=ErrorCode.SINGLE_INSTANCE_VIOLATION,
            ) from exc

    def resume(self, job_id: int) -> None:
        self.check_available()
        with session_scope() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            self.ensure_owner(session)
            job = require_job(session, job_id)
            pending = session.scalar(
                select(JobTarget.document_id)
                .where(JobTarget.job_id == job_id, JobTarget.status == TargetStatus.PENDING)
                .limit(1)
            )
            if job.status != JobStatus.INTERRUPTED or pending is None:
                raise PaperWrenchError(
                    "Only interrupted Jobs with unsent targets can resume.",
                    status_code=409,
                    code=ErrorCode.JOB_NOT_RESUMABLE,
                )
            job.status = JobStatus.PENDING
            job.finished_at = None
        self.wake.set()

    def _claim(self) -> tuple[int, int] | None:
        with session_scope() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            self.ensure_owner(session)
            target = session.scalar(
                select(JobTarget)
                .join(Job)
                .where(
                    Job.status.in_((JobStatus.PENDING, JobStatus.RUNNING)),
                    JobTarget.status == TargetStatus.PENDING,
                )
                .order_by(Job.id, JobTarget.position)
                .limit(1)
            )
            if target is None:
                return None
            target.status = TargetStatus.READING
            target.execution_owner = self.instance_id
            target.started_at = utcnow()
            job = require_job(session, target.job_id)
            job.status = JobStatus.RUNNING
            job.started_at = job.started_at or utcnow()
            job.heartbeat_at = utcnow()
            return target.job_id, target.document_id

    async def _worker(self) -> None:
        try:
            while not self.stopped:
                target = self._claim()
                if target is None:
                    self.wake.clear()
                    await self.wake.wait()
                    continue
                await self.execute(*target)
        except OwnershipLost:
            self.stop_scheduling()
        except asyncio.CancelledError:
            raise
        except Exception:
            # No raw upstream or SQL exception values in durable history/logs.
            logger.error("job_worker_stopped", reason="internal_failure")
            self.stop_scheduling()

    async def close(self) -> None:
        self.stop_scheduling()
        for worker in self.workers:
            worker.cancel()
        await asyncio.gather(*self.workers, return_exceptions=True)

    def _finish(
        self,
        job_id: int,
        document_id: int,
        status: TargetStatus,
        *,
        error: str | None = None,
        http_status: int | None = None,
        written: dict[str, FieldValue] | None = None,
    ) -> None:
        with session_scope() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            target = session.get(JobTarget, (job_id, document_id))
            assert target is not None
            # A new runtime may have recovered this target while HTTP was in flight.
            if target.execution_owner != self.instance_id or target.status not in ACTIVE_TARGETS:
                return
            target.status, target.error, target.http_status = status, error, http_status
            target.finished_at = utcnow()
            require_job(session, job_id).heartbeat_at = utcnow()
            operations = session.scalars(
                select(JobOperation).where(
                    JobOperation.job_id == job_id,
                    JobOperation.document_id == document_id,
                    JobOperation.status == OperationStatus.PENDING,
                )
            )
            for operation in operations:
                operation.status = OUTCOMES[status]
                operation.error, operation.http_status = error, http_status
                operation.finished_at = target.finished_at
                if written is not None:
                    operation.written_value_json = written[operation.field_key].model_dump_json()
            finalize(session, job_id)
        self.wake.set()

    def _before(
        self, job_id: int, current: Document, changes: list[ProposedChange], *, send: bool
    ) -> None:
        with session_scope() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            self.ensure_owner(session)
            target = session.get(JobTarget, (job_id, current.id))
            assert target is not None
            if target.execution_owner != self.instance_id or target.status != TargetStatus.READING:
                raise OwnershipLost
            target.before_custom_fields_json = encoded(
                [field.model_dump(mode="json") for field in current.custom_fields]
            )
            job = require_job(session, job_id)
            job.paperless_api_version = self.settings.paperless_api_version
            job.paperless_server_version = self.client.paperless_version
            if send:
                target.status = TargetStatus.WRITING
                target.attempts += 1
            by_key = {change.field.key: change for change in changes}
            for operation in session.scalars(
                select(JobOperation).where(
                    JobOperation.job_id == job_id, JobOperation.document_id == current.id
                )
            ):
                change = by_key[operation.field_key]
                operation.before_value_json = change.before.model_dump_json()
                operation.source_modified = (
                    datetime.fromisoformat(current.modified) if current.modified else None
                )
                operation.started_at = utcnow()
                if equal(change.before, change.intended):
                    operation.status = OperationStatus.SKIPPED_UNCHANGED
                    operation.finished_at = utcnow()
                elif send:
                    operation.attempts += 1

    def _interrupt_unsent(self, job_id: int, document_id: int) -> None:
        with session_scope() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            target = session.get(JobTarget, (job_id, document_id))
            if target is not None and target.execution_owner == self.instance_id:
                target.status = TargetStatus.PENDING
                target.execution_owner = None
                require_job(session, job_id).status = JobStatus.INTERRUPTED

    async def execute(self, job_id: int, document_id: int) -> None:
        sent = False
        acknowledged = False
        try:
            with session_scope() as session:
                target = session.get(JobTarget, (job_id, document_id))
                assert target is not None and target.preview_json is not None
                staged = PreviewRow.model_validate_json(target.preview_json)
                job = require_job(session, job_id)
                rollback = job.type == JobType.ROLLBACK
                spec = staged.rollback_spec if rollback else Transformation.model_validate(
                    {
                        "targets": {"source": "ids", "document_ids": [document_id]},
                        "operations": json.loads(job.transformation_json or "[]"),
                    }
                )
                assert spec is not None
                race_ack = job.acknowledge_external_race
            await self.registry.refresh(MetadataKind.CUSTOM_FIELD)
            definitions = {field.id: field for field in await self.registry.all_custom_fields()}
            if catalog_revision(list(definitions.values())) != staged.catalog_revision:
                raise PaperlessConflictError("Metadata changed since preview.")

            def prepare(current: Document) -> MutationPlan | None:
                nonlocal sent, acknowledged
                fresh = evaluate(current, spec, definitions)
                # Already at the reviewed target is a no-write observation, never
                # a claim that this runtime performed an earlier unknown write.
                if not rollback and all(
                    equal(c.before, p.intended)
                    for c, p in zip(fresh.changes, staged.changes, strict=True)
                ):
                    observed = [
                        c.model_copy(update={"intended": p.intended})
                        for c, p in zip(fresh.changes, staged.changes, strict=True)
                    ]
                    self._before(job_id, current, observed, send=False)
                    return None
                if (not rollback and revision(current) != staged.observed_revision) or any(
                    c.status == ResultStatus.ERROR
                    or not equal(c.before, p.before)
                    or not equal(c.intended, p.intended)
                    for c, p in zip(fresh.changes, staged.changes, strict=True)
                ):
                    raise PaperlessConflictError("Document changed since preview.")
                core: dict[str, Any] = {}
                updates: list[dict[str, Any]] = []
                removals: list[int] = []
                for change in fresh.changes:
                    if change.status == ResultStatus.UNCHANGED:
                        continue
                    assert change.intended is not None
                    if isinstance(change.field, CustomFieldRef):
                        if change.intended.kind == "absent":
                            removals.append(change.field.field_id)
                        else:
                            updates.append(
                                {"field": change.field.field_id, "value": change.intended.raw}
                            )
                    else:
                        core[change.field.name.value] = change.intended.raw

                def before_send() -> None:
                    nonlocal sent
                    self._before(job_id, current, fresh.changes, send=True)
                    sent = True

                def after_response() -> None:
                    nonlocal acknowledged
                    acknowledged = True

                return MutationPlan(
                    core=core,
                    custom_updates=updates,
                    remove_custom_fields=removals,
                    acknowledge_external_race=race_ack,
                    before_send=before_send,
                    after_response=after_response,
                    readback=True,
                )

            result = await self.client.mutate_document_with_plan(document_id, prepare)
            if result is None:
                self._finish(job_id, document_id, TargetStatus.UNCHANGED)
                return
            assert result.response is not None
            response_values = evaluate(result.response, spec, definitions).changes
            readback_values = evaluate(result.written, spec, definitions).changes
            if (
                result.response.id != document_id
                or result.written.id != document_id
                or any(
                    not equal(a.before, b.before)
                    for a, b in zip(response_values, readback_values, strict=True)
                )
            ):
                self._finish(
                    job_id, document_id, TargetStatus.AMBIGUOUS, error="READBACK_DISAGREES"
                )
                return
            self._finish(
                job_id,
                document_id,
                TargetStatus.SUCCEEDED,
                written={c.field.key: c.before for c in readback_values},
            )
        except (asyncio.CancelledError, OwnershipLost):
            if sent:
                self._finish(
                    job_id, document_id, TargetStatus.AMBIGUOUS, error="PROCESS_INTERRUPTED"
                )
            else:
                self._interrupt_unsent(job_id, document_id)
            raise
        except Exception as exc:
            code = str(exc.code) if isinstance(exc, PaperWrenchError) else "INTERNAL_FAILURE"
            upstream = (
                (exc.details or {}).get("upstream_status")
                if isinstance(exc, PaperWrenchError)
                else None
            )
            definite = upstream in (400, 401, 403, 404, 409, 422, 429)
            if sent and (acknowledged or not definite):
                status = TargetStatus.AMBIGUOUS
            elif isinstance(exc, PaperlessForbiddenError):
                status = TargetStatus.PERMISSION
            elif isinstance(exc, PaperlessNotFoundError):
                status = TargetStatus.MISSING
            elif isinstance(exc, PaperlessConflictError):
                status = TargetStatus.CONFLICT
            else:
                status = TargetStatus.FAILED
            self._finish(job_id, document_id, status, error=code, http_status=upstream)
