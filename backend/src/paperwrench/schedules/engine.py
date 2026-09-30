"""Single-instance dispatcher. Durable claims and jobs share one occurrence.

No occurrence is replayed after a crash. Read failures skip to the next calendar
occurrence; writes retain the job engine's verification and manual recovery.
"""

import asyncio

from sqlalchemy import select
from sqlalchemy import text

from paperwrench.auth.service import SessionStore
from paperwrench.db.base import utcnow
from paperwrench.db.models import Job
from paperwrench.db.models import JobStatus
from paperwrench.db.models import RuleSchedule
from paperwrench.db.models import SavedRule
from paperwrench.db.models import ScheduleRun
from paperwrench.db.session import session_scope
from paperwrench.errors import ErrorCode
from paperwrench.errors import PaperWrenchError
from paperwrench.jobs import store
from paperwrench.jobs.engine import JobEngine
from paperwrench.jobs.model import CreateJob
from paperwrench.logging import get_logger
from paperwrench.previews.service import PreviewService
from paperwrench.previews.service import stale
from paperwrench.schedules.recurrence import Recurrence

logger = get_logger(__name__)


class ScheduleEngine:
    def __init__(self, jobs: JobEngine, sessions: SessionStore, previews: PreviewService) -> None:
        self.jobs = jobs
        self.sessions = sessions
        self.previews = previews
        self.bindings: dict[int, str] = {}
        self.task: asyncio.Task[None] | None = None
        self.tick_lock = asyncio.Lock()

    def bind(self, schedule_id: int, session_id: str) -> None:
        self.bindings[schedule_id] = session_id

    def unbind(self, schedule_id: int) -> None:
        self.bindings.pop(schedule_id, None)

    def start(self) -> None:
        with session_scope() as db:
            for schedule in db.scalars(select(RuleSchedule).where(RuleSchedule.enabled.is_(True))):
                schedule.enabled = False
                schedule.status = "needs_approval"
                schedule.notification = "AUTH_REQUIRED"
            for run in db.scalars(select(ScheduleRun).where(ScheduleRun.status == "preparing")):
                run.status = "interrupted"
                run.error_code = "RESTARTED"
                run.finished_at = utcnow()
        self.task = asyncio.create_task(self._loop())

    async def close(self) -> None:
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        self.bindings.clear()

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(5)
            try:
                await self.tick()
            except Exception:
                logger.error("schedule_tick_failed", reason="internal_failure")

    def reconcile(self) -> None:
        with session_scope() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            self.jobs.ensure_owner(db)
            for run in db.scalars(select(ScheduleRun).where(ScheduleRun.status == "running")):
                job = db.get(Job, run.job_id)
                if job is None or job.status in (JobStatus.PENDING, JobStatus.RUNNING):
                    continue
                run.status = str(job.status)
                run.finished_at = utcnow()
                schedule = db.get(RuleSchedule, run.schedule_id)
                if schedule is not None and job.status != JobStatus.COMPLETED:
                    schedule.notification = "JOB_REQUIRES_ATTENTION"
                    # Never start another automatic write after uncertain/partial failure.
                    schedule.enabled = False
                    schedule.status = "needs_approval"

    async def tick(self) -> None:
        async with self.tick_lock:
            self.jobs.check_available()
            self.reconcile()
            with session_scope() as db:
                ids = list(db.scalars(select(RuleSchedule.id).where(
                    RuleSchedule.enabled.is_(True), RuleSchedule.next_run_at <= utcnow(),
                ).order_by(RuleSchedule.next_run_at).limit(100)))
            for schedule_id in ids:
                run_id = self.claim(schedule_id)
                if run_id is not None:
                    await self.execute(run_id)

    def claim(self, schedule_id: int) -> int | None:
        with session_scope() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            self.jobs.ensure_owner(db)
            schedule = db.get(RuleSchedule, schedule_id)
            now = utcnow()
            if schedule is None or not schedule.enabled or schedule.next_run_at > now:
                return None
            previous = db.scalar(select(ScheduleRun.id).where(
                ScheduleRun.schedule_id == schedule_id,
                ScheduleRun.status.in_(("preparing", "running")),
            ).limit(1))
            run = ScheduleRun(
                schedule_id=schedule_id, owner_id=schedule.owner_id,
                scheduled_for=schedule.next_run_at, rule_revision=schedule.rule_revision,
                approval_preview_id=schedule.approval_preview_id,
                status="skipped" if previous is not None else "preparing",
                error_code="CONFLICT" if previous is not None else None,
                finished_at=now if previous is not None else None,
            )
            db.add(run)
            schedule.next_run_at = Recurrence.model_validate_json(
                schedule.recurrence_json,
            ).next_after(now)
            db.flush()
            return run.id if previous is None else None

    async def execute(self, run_id: int) -> None:
        # Imports avoid coupling application construction to the dispatcher.
        from paperwrench.api.v1.rules import RuleDefinition
        from paperwrench.api.v1.rules import _spec
        from paperwrench.api.v1.rules import build_rule_preview

        preview_id: str | None = None
        with session_scope() as db:
            run = db.get(ScheduleRun, run_id)
            assert run is not None
            schedule = db.get(RuleSchedule, run.schedule_id)
            assert schedule is not None
            owner_id, schedule_id = run.owner_id, schedule.id
            rule_id, approved_spec = schedule.rule_id, schedule.approved_spec_json
            race = schedule.acknowledge_external_race
        try:
            auth = await self.sessions.resolve(self.bindings.get(schedule_id))
            if auth.paperless_user_id != owner_id:
                raise stale("Schedule credentials do not belong to its owner.")
            # Force upstream credential verification for every occurrence.
            await auth.client.get_profile()
            with session_scope() as db:
                rule = db.get(SavedRule, rule_id)
                if rule is None or rule.owner_id != owner_id or rule.revision != run.rule_revision:
                    raise stale("Rule changed; a new approval is required.")
                spec = _spec(db, RuleDefinition.model_validate_json(rule.definition_json), owner_id)
                if spec.model_dump_json() != approved_spec:
                    raise stale("Collection changed; a new approval is required.")
            result = await build_rule_preview(
                rule_id, self.previews, auth.client, auth.registry, owner_id,
            )
            preview_id = result.id
            if result.transformation.model_dump_json() != approved_spec or result.errors:
                raise stale("Fresh preview no longer matches the approval or contains errors.")
            if not result.changed:
                self.finish(run_id, "unchanged")
                return
            # Recheck logout/expiry after potentially long read-only preparation.
            await self.sessions.resolve(self.bindings.get(schedule_id))
            self.jobs.check_available()
            body = CreateJob(
                preview_id=result.id, preview_token=result.preview_token,
                transformation=result.transformation, target_fingerprint=result.target_fingerprint,
                result_fingerprint=result.result_fingerprint, version=result.version,
                acknowledge=True, acknowledge_external_race=race,
            )
            job_id = store.create_job(body, owner_id, rule_id, schedule_run_id=run_id)
            self.jobs.bind(job_id, owner_id, auth.client, auth.registry)
            self.jobs.wake.set()
        except PaperWrenchError as exc:
            pause = exc.code not in {
                ErrorCode.PAPERLESS_UNREACHABLE, ErrorCode.PAPERLESS_ERROR, ErrorCode.CONFLICT,
            }
            self.finish(run_id, "failed", str(exc.code), pause=pause)
        except Exception:
            logger.error("schedule_occurrence_failed", run_id=run_id, reason="internal_failure")
            self.finish(run_id, "failed", "INTERNAL_ERROR", pause=True)
        finally:
            if preview_id is not None:
                self.previews.discard(preview_id, owner_id)

    def finish(
        self, run_id: int, status: str, error: str | None = None, *, pause: bool = False,
    ) -> None:
        with session_scope() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            self.jobs.ensure_owner(db)
            run = db.get(ScheduleRun, run_id)
            assert run is not None
            # A committed job always owns recovery, even if dispatch was interrupted.
            if run.job_id is not None:
                return
            run.status = status
            run.error_code = error
            run.finished_at = utcnow()
            schedule = db.get(RuleSchedule, run.schedule_id)
            if schedule is not None:
                if error:
                    schedule.notification = error
                if pause and schedule.approval_preview_id == run.approval_preview_id:
                    schedule.enabled = False
                    schedule.status = "needs_approval"
