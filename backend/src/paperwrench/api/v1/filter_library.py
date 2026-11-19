"""Owner-scoped, reusable copies of filter expressions."""

from typing import Annotated
from typing import Literal

from fastapi import APIRouter
from fastapi import Depends
from fastapi import Response
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import StringConstraints
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from paperwrench.api.deps import get_metadata_registry
from paperwrench.api.deps import get_owner_id
from paperwrench.db.models import SavedFilterSet
from paperwrench.db.session import get_db
from paperwrench.errors import ErrorCode
from paperwrench.errors import NotFoundError
from paperwrench.errors import PaperWrenchError
from paperwrench.filters import FilterSet
from paperwrench.filters import build_catalog
from paperwrench.filters import validate_and_compile
from paperwrench.filters.compiler import leaf_sources
from paperwrench.paperless import MetadataRegistry
from paperwrench.paperless.models import MetadataKind

router = APIRouter(prefix="/filter-library", tags=["filter-library"])


class FilterDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    filters: FilterSet


class FilterEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
    description: Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)] = ""
    definition: FilterDefinition


class FilterView(FilterEntry):
    id: int


def _view(row: SavedFilterSet) -> FilterView:
    return FilterView(
        id=row.id, name=row.name, description=row.description,
        definition=FilterDefinition.model_validate_json(row.definition_json),
    )


def _owned(db: Session, entry_id: int, owner_id: int) -> SavedFilterSet:
    row = db.get(SavedFilterSet, entry_id)
    if row is None or row.owner_id != owner_id:
        raise NotFoundError("Saved filter set not found.")
    return row


async def _save(
    data: FilterEntry, row: SavedFilterSet, db: Session, registry: MetadataRegistry,
) -> FilterView:
    filters = data.definition.filters
    validate_and_compile(filters, await build_catalog(registry, filters))
    row.name = data.name
    row.description = data.description
    row.definition_json = data.definition.model_dump_json()
    db.add(row)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise PaperWrenchError(
            "A filter set with this name already exists.",
            status_code=409, code=ErrorCode.CONFLICT,
        ) from exc
    return _view(row)


@router.get("", response_model=list[FilterView])
def list_entries(
    db: Session = Depends(get_db), owner_id: int = Depends(get_owner_id),
) -> list[FilterView]:
    rows = db.scalars(select(SavedFilterSet).where(SavedFilterSet.owner_id == owner_id)
                      .order_by(SavedFilterSet.name))
    return [_view(row) for row in rows]


@router.get("/{entry_id}/criteria", response_model=FilterDefinition)
async def resolve_entry(
    entry_id: int, db: Session = Depends(get_db), owner_id: int = Depends(get_owner_id),
    registry: MetadataRegistry = Depends(get_metadata_registry),
) -> FilterDefinition:
    definition = _view(_owned(db, entry_id, owner_id)).definition
    filters = definition.filters
    if True in leaf_sources(filters.root):
        await registry.refresh(MetadataKind.CUSTOM_FIELD)
    validate_and_compile(filters, await build_catalog(registry, filters))
    return definition


@router.post("", status_code=201, response_model=FilterView)
async def create_entry(
    data: FilterEntry, db: Session = Depends(get_db), owner_id: int = Depends(get_owner_id),
    registry: MetadataRegistry = Depends(get_metadata_registry),
) -> FilterView:
    return await _save(data, SavedFilterSet(owner_id=owner_id), db, registry)


@router.put("/{entry_id}", response_model=FilterView)
async def update_entry(
    entry_id: int, data: FilterEntry,
    db: Session = Depends(get_db), owner_id: int = Depends(get_owner_id),
    registry: MetadataRegistry = Depends(get_metadata_registry),
) -> FilterView:
    return await _save(data, _owned(db, entry_id, owner_id), db, registry)


@router.delete("/{entry_id}", status_code=204)
def delete_entry(
    entry_id: int, db: Session = Depends(get_db), owner_id: int = Depends(get_owner_id),
) -> Response:
    db.delete(_owned(db, entry_id, owner_id))
    db.commit()
    return Response(status_code=204)
