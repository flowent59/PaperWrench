"""Private saved Explorer views."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from fastapi import Depends
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
from paperwrench.api.v1.documents import build_query_params
from paperwrench.db.base import utcnow
from paperwrench.db.models import SavedExplorerView
from paperwrench.db.session import get_db
from paperwrench.errors import ErrorCode
from paperwrench.errors import PaperWrenchError
from paperwrench.filters import DatasetPageRequest
from paperwrench.filters import DatasetQuery
from paperwrench.paperless import MetadataRegistry

router = APIRouter(prefix="/views", tags=["views"])
PageSize = Literal[25, 50, 100, 250]


class ExplorerViewDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: DatasetQuery = Field(default_factory=DatasetQuery)
    page_size: PageSize = 100
    column_visibility: dict[str, bool] = Field(default_factory=dict, max_length=200)


class ExplorerViewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=255)
    definition: ExplorerViewDefinition
    is_default: bool = False

    @field_validator("name")
    @classmethod
    def nonblank_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Name must not be blank.")
        return value


class ExplorerViewResponse(ExplorerViewInput):
    id: int
    created_at: str
    updated_at: str


def _view(row: SavedExplorerView) -> ExplorerViewResponse:
    return ExplorerViewResponse(
        id=row.id,
        name=row.name,
        definition=ExplorerViewDefinition.model_validate_json(row.definition_json),
        is_default=row.is_default,
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
    )


def _row(db: Session, view_id: int, owner_id: int) -> SavedExplorerView:
    row = db.get(SavedExplorerView, view_id)
    if row is None or row.owner_id != owner_id:
        raise PaperWrenchError("Saved view not found.", status_code=404, code=ErrorCode.NOT_FOUND)
    return row


def _save(row: SavedExplorerView, data: ExplorerViewInput, db: Session) -> None:
    row.name = data.name
    row.definition_json = data.definition.model_dump_json()
    row.is_default = data.is_default
    row.updated_at = utcnow()
    db.add(row)
    try:
        # Ensure a newly added row has an id before clearing other defaults.
        db.flush()
        if row.is_default:
            db.execute(
                update(SavedExplorerView)
                .where(
                    SavedExplorerView.owner_id == row.owner_id,
                    SavedExplorerView.id != row.id,
                )
                .values(is_default=False)
            )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise PaperWrenchError(
            "A saved view with this name already exists.",
            status_code=409,
            code=ErrorCode.CONFLICT,
        ) from exc
    db.refresh(row)


async def _validate(data: ExplorerViewInput, registry: MetadataRegistry) -> None:
    query = data.definition.query
    await build_query_params(
        DatasetPageRequest(
            search=query.search,
            filters=query.filters,
            ordering=query.ordering,
            page_size=data.definition.page_size,
        ),
        registry=registry,
    )


@router.get("", response_model=list[ExplorerViewResponse])
def list_views(
    db: Session = Depends(get_db), owner_id: int = Depends(get_owner_id)
) -> list[ExplorerViewResponse]:
    rows = db.scalars(
        select(SavedExplorerView)
        .where(SavedExplorerView.owner_id == owner_id)
        .order_by(SavedExplorerView.name)
    ).all()
    return [_view(row) for row in rows]


@router.post("", response_model=ExplorerViewResponse, status_code=201)
async def create_view(
    data: ExplorerViewInput,
    registry: MetadataRegistry = Depends(get_metadata_registry),
    db: Session = Depends(get_db),
    owner_id: int = Depends(get_owner_id),
) -> ExplorerViewResponse:
    await _validate(data, registry)
    row = SavedExplorerView(
        owner_id=owner_id,
        name=data.name,
        definition_json=data.definition.model_dump_json(),
        is_default=data.is_default,
    )
    db.add(row)
    _save(row, data, db)
    return _view(row)


@router.put("/{view_id}", response_model=ExplorerViewResponse)
async def update_view(
    view_id: int,
    data: ExplorerViewInput,
    registry: MetadataRegistry = Depends(get_metadata_registry),
    db: Session = Depends(get_db),
    owner_id: int = Depends(get_owner_id),
) -> ExplorerViewResponse:
    row = _row(db, view_id, owner_id)
    await _validate(data, registry)
    _save(row, data, db)
    return _view(row)


@router.delete("/{view_id}", status_code=204)
def delete_view(
    view_id: int,
    db: Session = Depends(get_db),
    owner_id: int = Depends(get_owner_id),
) -> Response:
    db.delete(_row(db, view_id, owner_id))
    db.commit()
    return Response(status_code=204)
