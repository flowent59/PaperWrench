"""Owner-scoped schedules; enabling always consumes a reviewed rule preview."""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter
from fastapi import Depends
from fastapi import Query
from fastapi import Request
from pydantic import BaseModel
from pydantic import ConfigDict
from sqlalchemy import select
from sqlalchemy import text

from paperwrench.api.deps import get_auth_session
from paperwrench.api.deps import get_owner_id
from paperwrench.api.v1.previews import no_cache
from paperwrench.api.v1.rules import RuleDefinition
from paperwrench.api.v1.rules import _row as rule_row
from paperwrench.api.v1.rules import _spec
from paperwrench.auth.service import AuthSession
from paperwrench.db.base import utcnow
from paperwrench.db.models import Job
from paperwrench.db.models import Preview
from paperwrench.db.models import RuleSchedule
from paperwrench.db.models import ScheduleRun
from paperwrench.db.session import session_scope
from paperwrench.errors import NotFoundError
from paperwrench.filters.model import CustomFieldRef
from paperwrench.jobs.model import CreateJob
from paperwrench.previews.service import PreviewService
from paperwrench.previews.service import limit
from paperwrench.previews.service import stale
from paperwrench.schedules.recurrence import Recurrence

router = APIRouter(prefix="/schedules", tags=["schedules"], dependencies=[Depends(no_cache)])


class ApproveSchedule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rule_id: int
    recurrence: Recurrence
    preview: CreateJob
    acknowledge_unattended: Literal[True]


class ScheduleView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    rule_id: int
    rule_revision: int
    approved_at: datetime
    enabled: bool
    next_run_at: datetime
    status: str
    notification: str | None
    recurrence: Recurrence


class RunView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    scheduled_for: datetime
    started_at: datetime
    finished_at: datetime | None
    rule_revision: int
    status: str
    error_code: str | None
    job_id: int | None


def view(row: RuleSchedule) -> ScheduleView:
    return ScheduleView(
        id=row.id, rule_id=row.rule_id, rule_revision=row.rule_revision,
        approved_at=row.approved_at, enabled=row.enabled, next_run_at=row.next_run_at,
        status=row.status, notification=row.notification,
        recurrence=Recurrence.model_validate_json(row.recurrence_json),
    )


@router.get("", response_model=list[ScheduleView])
def list_schedules(owner_id: int = Depends(get_owner_id)) -> list[ScheduleView]:
    with session_scope() as db:
        return [view(row) for row in db.scalars(
            select(RuleSchedule).where(RuleSchedule.owner_id == owner_id)
            .order_by(RuleSchedule.id.desc())
        )]


@router.post("", response_model=ScheduleView, status_code=201)
async def approve(
    data: ApproveSchedule, request: Request, auth: AuthSession = Depends(get_auth_session),
) -> ScheduleView:
    request.app.state.jobs.check_available()
    owner_id = auth.paperless_user_id
    body = data.preview
    with session_scope() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        rule = rule_row(db, data.rule_id, owner_id)
        spec = _spec(db, RuleDefinition.model_validate_json(rule.definition_json), owner_id)
        preview = db.get(Preview, body.preview_id)
        if (
            preview is None or preview.rule_id != rule.id
            or preview.rule_revision != rule.revision
            or preview.rule_spec_json != spec.model_dump_json()
            or body.transformation != spec
        ):
            raise stale("Review the current rule and collection before scheduling.")
        if any(isinstance(op.field, CustomFieldRef) for op in spec.operations) and (
            not body.acknowledge_external_race
        ):
            raise limit("Custom writes require acknowledgement of the external-writer race.")
        PreviewService().claim(db, body.preview_id, body, owner_id, allow_unchanged=True)
        row = db.scalar(select(RuleSchedule).where(RuleSchedule.rule_id == rule.id))
        if row is None:
            row = RuleSchedule(owner_id=owner_id, rule_id=rule.id)
            db.add(row)
        row.rule_revision = rule.revision
        row.approved_spec_json = spec.model_dump_json()
        row.approval_preview_id = body.preview_id
        row.approved_at = utcnow()
        row.recurrence_json = data.recurrence.model_dump_json()
        row.enabled = True
        row.status = "enabled"
        row.notification = None
        row.acknowledge_external_race = body.acknowledge_external_race
        row.next_run_at = data.recurrence.next_after(utcnow())
        db.flush()
        result = view(row)
    request.app.state.schedules.bind(result.id, auth.session_id)
    return result


@router.post("/{schedule_id}/disable", response_model=ScheduleView)
def disable(
    schedule_id: int, request: Request, owner_id: int = Depends(get_owner_id),
) -> ScheduleView:
    with session_scope() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        row = db.get(RuleSchedule, schedule_id)
        if row is None or row.owner_id != owner_id:
            raise NotFoundError("Schedule not found.")
        row.enabled = False
        row.status = "disabled"
        result = view(row)
    request.app.state.schedules.unbind(schedule_id)
    return result


@router.post("/{schedule_id}/acknowledge", response_model=ScheduleView)
def acknowledge(schedule_id: int, owner_id: int = Depends(get_owner_id)) -> ScheduleView:
    with session_scope() as db:
        row = db.get(RuleSchedule, schedule_id)
        if row is None or row.owner_id != owner_id:
            raise NotFoundError("Schedule not found.")
        row.notification = None
        return view(row)


@router.get("/{schedule_id}/runs", response_model=list[RunView])
def runs(
    schedule_id: int, before: int | None = Query(default=None, gt=0),
    owner_id: int = Depends(get_owner_id),
) -> list[RunView]:
    with session_scope() as db:
        row = db.get(RuleSchedule, schedule_id)
        if row is None or row.owner_id != owner_id:
            raise NotFoundError("Schedule not found.")
        query = select(ScheduleRun).where(
            ScheduleRun.schedule_id == schedule_id, ScheduleRun.owner_id == owner_id,
        )
        if before is not None:
            query = query.where(ScheduleRun.id < before)
        items = []
        for run in db.scalars(query.order_by(ScheduleRun.id.desc()).limit(50)):
            item = RunView.model_validate(run)
            job = db.get(Job, run.job_id) if run.job_id is not None else None
            if job is not None and job.owner_id == owner_id:
                item.status = str(job.status)
                item.finished_at = job.finished_at
            items.append(item)
        return items
