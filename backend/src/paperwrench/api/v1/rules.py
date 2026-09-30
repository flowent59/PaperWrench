"""Private, manually triggered bulk rules. Every run uses the normal preview/job path."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated
from typing import Literal

from fastapi import APIRouter
from fastapi import Depends
from fastapi import Request
from fastapi import Response
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import field_validator
from sqlalchemy import select
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from paperwrench.api.deps import get_metadata_registry
from paperwrench.api.deps import get_owner_id
from paperwrench.api.deps import get_paperless_client
from paperwrench.api.v1.collections import _row as collection_row
from paperwrench.api.v1.documents import build_query_params
from paperwrench.api.v1.previews import no_cache
from paperwrench.db.base import utcnow
from paperwrench.db.models import CollectionDocument
from paperwrench.db.models import CollectionKind
from paperwrench.db.models import Preview
from paperwrench.db.models import SavedRule
from paperwrench.db.models import SavedRuleRevision
from paperwrench.db.session import get_db
from paperwrench.db.session import session_scope
from paperwrench.errors import ErrorCode
from paperwrench.errors import PaperWrenchError
from paperwrench.filters.model import DatasetPageRequest
from paperwrench.filters.model import DatasetQuery
from paperwrench.jobs import store
from paperwrench.jobs.engine import JobEngine
from paperwrench.jobs.model import CreateJob
from paperwrench.jobs.model import JobView
from paperwrench.paperless import MetadataRegistry
from paperwrench.paperless import PaperlessClient
from paperwrench.previews.model import MAX_DOCUMENTS
from paperwrench.previews.model import CreatedPreview
from paperwrench.previews.service import PreviewService
from paperwrench.previews.service import stale
from paperwrench.transformations import validate
from paperwrench.transformations.model import DatasetTargets
from paperwrench.transformations.model import ExplicitTargets
from paperwrench.transformations.model import Operation
from paperwrench.transformations.model import Transformation

router = APIRouter(prefix="/rules", tags=["rules"], dependencies=[Depends(no_cache)])


class FilterTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["filter"] = "filter"
    query: DatasetQuery


class CollectionTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["collection"] = "collection"
    collection_id: int = Field(gt=0)


class RuleDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: Annotated[FilterTarget | CollectionTarget, Field(discriminator="kind")]
    operations: list[Operation] = Field(min_length=1, max_length=100)


class RuleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    definition: RuleDefinition

    @field_validator("name")
    @classmethod
    def nonblank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Name must not be blank.")
        return value


class RuleUpdate(RuleInput):
    expected_revision: int = Field(ge=1)


class RuleView(RuleInput):
    id: int
    revision: int
    created_at: datetime
    updated_at: datetime


class RuleRevisionView(RuleInput):
    revision: int
    created_at: datetime


class RuleCreatedPreview(CreatedPreview):
    transformation: Transformation


def _row(db: Session, rule_id: int, owner_id: int) -> SavedRule:
    row = db.get(SavedRule, rule_id)
    if row is None or row.owner_id != owner_id:
        raise PaperWrenchError("Rule not found.", status_code=404, code=ErrorCode.NOT_FOUND)
    return row


def _view(row: SavedRule) -> RuleView:
    return RuleView(
        id=row.id, name=row.name, description=row.description,
        definition=RuleDefinition.model_validate_json(row.definition_json),
        revision=row.revision, created_at=row.created_at, updated_at=row.updated_at,
    )


def _revision(db: Session, row: SavedRule) -> None:
    db.add(SavedRuleRevision(
        rule_id=row.id, owner_id=row.owner_id, revision=row.revision,
        name=row.name, description=row.description,
        definition_json=row.definition_json,
    ))


def _commit(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise PaperWrenchError(
            "A rule with this name already exists.", status_code=409, code=ErrorCode.CONFLICT
        ) from exc


def _flush(db: Session) -> None:
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise PaperWrenchError(
            "A rule with this name already exists.", status_code=409, code=ErrorCode.CONFLICT
        ) from exc


def _spec(db: Session, definition: RuleDefinition, owner_id: int) -> Transformation:
    target = definition.target
    targets: DatasetTargets | ExplicitTargets
    if isinstance(target, FilterTarget):
        if (
            (target.query.filters is None or target.query.filters.is_empty)
            and (target.query.search is None or not target.query.search.text.strip())
        ):
            raise PaperWrenchError(
                "A saved rule needs an explicit filter or search.",
                status_code=422, code=ErrorCode.VALIDATION_ERROR,
            )
        targets = DatasetTargets(query=target.query)
    else:
        collection = collection_row(db, target.collection_id, owner_id)
        if collection.kind == CollectionKind.DYNAMIC:
            query = DatasetQuery.model_validate(
                {"filters": json.loads(collection.filterset_json or "null")}
            )
            targets = DatasetTargets(query=query)
        else:
            ids = db.scalars(
                select(CollectionDocument.document_id)
                .where(CollectionDocument.collection_id == collection.id)
                .order_by(CollectionDocument.document_id)
                .limit(MAX_DOCUMENTS + 1)
            ).all()
            if not ids or len(ids) > MAX_DOCUMENTS:
                raise PaperWrenchError(
                    "Collection must contain between 1 and 100000 documents.",
                    status_code=422, code=ErrorCode.VALIDATION_ERROR,
                )
            targets = ExplicitTargets(document_ids=list(ids))
    return Transformation(targets=targets, operations=definition.operations)


async def _validate(
    db: Session, definition: RuleDefinition, owner_id: int, registry: MetadataRegistry
) -> None:
    await _validate_spec(_spec(db, definition, owner_id), registry)


async def _validate_spec(spec: Transformation, registry: MetadataRegistry) -> None:
    issues = validate(spec, {field.id: field for field in await registry.all_custom_fields()})
    if issues:
        raise PaperWrenchError(
            "Rule transformation is invalid.", status_code=422,
            code=ErrorCode.VALIDATION_ERROR,
            details={"issues": [issue.model_dump() for issue in issues]},
        )
    if isinstance(spec.targets, DatasetTargets):
        await build_query_params(
            DatasetPageRequest(**spec.targets.query.model_dump()), registry=registry
        )


@router.get("", response_model=list[RuleView])
def list_rules(
    db: Session = Depends(get_db), owner_id: int = Depends(get_owner_id)
) -> list[RuleView]:
    return [_view(row) for row in db.scalars(
        select(SavedRule).where(SavedRule.owner_id == owner_id).order_by(SavedRule.name)
    )]


@router.post("", response_model=RuleView, status_code=201)
async def create_rule(
    data: RuleInput, db: Session = Depends(get_db),
    registry: MetadataRegistry = Depends(get_metadata_registry),
    owner_id: int = Depends(get_owner_id),
) -> RuleView:
    await _validate(db, data.definition, owner_id, registry)
    row = SavedRule(
        owner_id=owner_id, name=data.name, description=data.description,
        definition_json=data.definition.model_dump_json(), revision=1,
    )
    db.add(row)
    _flush(db)
    _revision(db, row)
    _commit(db)
    return _view(row)


@router.get("/{rule_id}", response_model=RuleView)
def get_rule(
    rule_id: int, db: Session = Depends(get_db), owner_id: int = Depends(get_owner_id)
) -> RuleView:
    return _view(_row(db, rule_id, owner_id))


@router.put("/{rule_id}", response_model=RuleView)
async def update_rule(
    rule_id: int, data: RuleUpdate, db: Session = Depends(get_db),
    registry: MetadataRegistry = Depends(get_metadata_registry),
    owner_id: int = Depends(get_owner_id),
) -> RuleView:
    row = _row(db, rule_id, owner_id)
    if row.revision != data.expected_revision:
        raise PaperWrenchError(
            "Rule changed since it was loaded.", status_code=409, code=ErrorCode.CONFLICT
        )
    await _validate(db, data.definition, owner_id, registry)
    try:
        changed = db.execute(
            update(SavedRule)
            .where(
                SavedRule.id == rule_id,
                SavedRule.owner_id == owner_id,
                SavedRule.revision == data.expected_revision,
            )
            .values(
                name=data.name, description=data.description,
                definition_json=data.definition.model_dump_json(),
                revision=data.expected_revision + 1, updated_at=utcnow(),
            )
            .returning(SavedRule.id)
        ).scalar_one_or_none()
    except IntegrityError as exc:
        db.rollback()
        raise PaperWrenchError(
            "A rule with this name already exists.", status_code=409, code=ErrorCode.CONFLICT
        ) from exc
    if changed is None:
        raise PaperWrenchError(
            "Rule changed since it was loaded.", status_code=409, code=ErrorCode.CONFLICT
        )
    db.refresh(row)
    _revision(db, row)
    _commit(db)
    return _view(row)


@router.get("/{rule_id}/revisions", response_model=list[RuleRevisionView])
def revisions(
    rule_id: int, db: Session = Depends(get_db), owner_id: int = Depends(get_owner_id)
) -> list[RuleRevisionView]:
    _row(db, rule_id, owner_id)
    return [RuleRevisionView(
        revision=item.revision, name=item.name, description=item.description,
        definition=RuleDefinition.model_validate_json(item.definition_json),
        created_at=item.created_at,
    ) for item in db.scalars(
        select(SavedRuleRevision)
        .where(SavedRuleRevision.rule_id == rule_id, SavedRuleRevision.owner_id == owner_id)
        .order_by(SavedRuleRevision.revision.desc())
    )]


@router.delete("/{rule_id}", status_code=204)
def delete_rule(
    rule_id: int, db: Session = Depends(get_db), owner_id: int = Depends(get_owner_id)
) -> Response:
    db.delete(_row(db, rule_id, owner_id))
    db.commit()
    return Response(status_code=204)


@router.post("/{rule_id}/preview", response_model=RuleCreatedPreview, status_code=201)
async def preview_rule(
    rule_id: int, request: Request,
    client: PaperlessClient = Depends(get_paperless_client),
    registry: MetadataRegistry = Depends(get_metadata_registry),
    owner_id: int = Depends(get_owner_id),
) -> RuleCreatedPreview:
    with session_scope() as db:
        row = _row(db, rule_id, owner_id)
        revision = row.revision
        definition = RuleDefinition.model_validate_json(row.definition_json)
        spec = _spec(db, definition, owner_id)
    await _validate_spec(spec, registry)
    service: PreviewService = request.app.state.previews
    result = await service.create(spec, client, registry, owner_id=owner_id)
    with session_scope() as db:
        row = _row(db, rule_id, owner_id)
        if row.revision != revision:
            service.discard(result.id, owner_id)
            raise stale("Rule changed during preview; review again.")
        preview = db.get(Preview, result.id)
        assert preview is not None
        preview.rule_id = rule_id
        preview.rule_revision = revision
        preview.rule_spec_json = spec.model_dump_json()
    return RuleCreatedPreview(**result.model_dump(), transformation=spec)


@router.post("/{rule_id}/apply", response_model=JobView, status_code=201)
async def apply_rule(
    rule_id: int, body: CreateJob, request: Request,
    client: PaperlessClient = Depends(get_paperless_client),
    registry: MetadataRegistry = Depends(get_metadata_registry),
    owner_id: int = Depends(get_owner_id),
) -> JobView:
    worker: JobEngine = request.app.state.jobs
    worker.check_available()
    job_id = store.create_job(body, owner_id, rule_id=rule_id)
    worker.bind(job_id, owner_id, client, registry)
    worker.wake.set()
    return store.job_view(job_id, owner_id)
