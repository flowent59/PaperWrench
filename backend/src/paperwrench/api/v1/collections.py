"""User-owned static and dynamically-filtered Paperless collections."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi import Depends
from fastapi import Query
from fastapi import Response
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import field_validator
from pydantic import model_validator
from sqlalchemy import delete
from sqlalchemy import func
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from paperwrench.api.deps import get_metadata_registry
from paperwrench.api.deps import get_owner_id
from paperwrench.api.deps import get_paperless_client
from paperwrench.api.v1.documents import DocumentListItem
from paperwrench.api.v1.documents import _document_to_list_item
from paperwrench.db.base import utcnow
from paperwrench.db.models import Collection
from paperwrench.db.models import CollectionDocument
from paperwrench.db.models import CollectionKind
from paperwrench.db.session import get_db
from paperwrench.errors import ErrorCode
from paperwrench.errors import PaperlessForbiddenError
from paperwrench.errors import PaperWrenchError
from paperwrench.filters import FilterSet
from paperwrench.filters import build_catalog
from paperwrench.filters import validate_and_compile
from paperwrench.paperless import MetadataRegistry
from paperwrench.paperless import PaperlessClient
from paperwrench.paperless import PaperlessNotFoundError

router = APIRouter(prefix="/collections", tags=["collections"])


class CollectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None

    @field_validator("name")
    @classmethod
    def nonblank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Name must not be blank.")
        return value


class CollectionCreate(CollectionInput):
    kind: CollectionKind = CollectionKind.STATIC
    document_ids: list[int] = Field(default_factory=list, max_length=500)
    filters: FilterSet | None = None

    @model_validator(mode="after")
    def consistent_kind(self) -> CollectionCreate:
        if self.kind is CollectionKind.DYNAMIC and self.filters is None:
            raise ValueError("A dynamic collection requires filters.")
        if self.kind is CollectionKind.DYNAMIC and self.document_ids:
            raise ValueError("A dynamic collection cannot contain stored document IDs.")
        if self.kind is CollectionKind.STATIC and self.filters is not None:
            raise ValueError("A static collection cannot contain filters.")
        return self


class CollectionUpdate(CollectionInput):
    filters: FilterSet | None = None


class CollectionPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filters: FilterSet
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=25, ge=1, le=100)


class MemberIds(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_ids: list[int] = Field(min_length=1, max_length=500)

    @field_validator("document_ids")
    @classmethod
    def positive_ids(cls, values: list[int]) -> list[int]:
        if any(value < 1 for value in values):
            raise ValueError("Document IDs must be positive.")
        return list(dict.fromkeys(values))


class CollectionView(CollectionInput):
    id: int
    kind: CollectionKind
    filters: FilterSet | None
    member_count: int
    created_at: str
    updated_at: str


class MemberView(BaseModel):
    document_id: int
    document: DocumentListItem | None
    available: bool


class MemberPage(BaseModel):
    items: list[MemberView]
    page: int
    page_size: int
    total: int
    page_count: int


def _row(db: Session, collection_id: int, owner_id: int) -> Collection:
    row = db.get(Collection, collection_id)
    if row is None or row.owner_id != owner_id:
        raise PaperWrenchError(
            "Collection not found.", status_code=404, code=ErrorCode.NOT_FOUND
        )
    return row


def _filters(row: Collection) -> FilterSet:
    if row.filterset_json is None:
        raise PaperWrenchError(
            "Dynamic collection has no filter definition.",
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
        )
    return FilterSet.model_validate_json(row.filterset_json)


async def _filter_params(filters: FilterSet, registry: MetadataRegistry) -> dict[str, Any]:
    return dict(validate_and_compile(filters, await build_catalog(registry)).params)


async def _view(
    db: Session, row: Collection, client: PaperlessClient, registry: MetadataRegistry
) -> CollectionView:
    filters = _filters(row) if row.kind == CollectionKind.DYNAMIC else None
    if filters is None:
        count = db.scalar(
            select(func.count()).select_from(CollectionDocument).where(
                CollectionDocument.collection_id == row.id
            )
        ) or 0
    else:
        count = await client.count_documents(params=await _filter_params(filters, registry))
    return CollectionView(
        id=row.id,
        name=row.name,
        description=row.description,
        kind=row.kind,
        filters=filters,
        member_count=count,
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
    )


def _commit(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise PaperWrenchError(
            "A collection with this name already exists.",
            status_code=409,
            code=ErrorCode.CONFLICT,
        ) from exc


async def _check_visible(ids: list[int], client: PaperlessClient) -> None:
    for document_id in ids:
        try:
            await client.get_document(document_id)
        except (PaperlessNotFoundError, PaperlessForbiddenError) as exc:
            raise PaperWrenchError(
                "One or more documents are unavailable.",
                status_code=422,
                code=ErrorCode.VALIDATION_ERROR,
            ) from exc


async def _dynamic_page(
    filters: FilterSet,
    page: int,
    page_size: int,
    client: PaperlessClient,
    registry: MetadataRegistry,
) -> MemberPage:
    result = await client.list_documents(
        params=await _filter_params(filters, registry), page=page, page_size=page_size
    )
    return MemberPage(
        items=[
            MemberView(
                document_id=document.id,
                document=await _document_to_list_item(document, registry),
                available=True,
            )
            for document in result.results
        ],
        page=page,
        page_size=page_size,
        total=result.count,
        page_count=(result.count + page_size - 1) // page_size,
    )


@router.get("", response_model=list[CollectionView])
async def list_collections(
    db: Session = Depends(get_db),
    client: PaperlessClient = Depends(get_paperless_client),
    registry: MetadataRegistry = Depends(get_metadata_registry),
    owner_id: int = Depends(get_owner_id),
) -> list[CollectionView]:
    rows = db.scalars(
        select(Collection).where(Collection.owner_id == owner_id).order_by(Collection.name)
    ).all()
    return [await _view(db, row, client, registry) for row in rows]


@router.post("/preview", response_model=MemberPage)
async def preview_dynamic_collection(
    data: CollectionPreviewRequest,
    client: PaperlessClient = Depends(get_paperless_client),
    registry: MetadataRegistry = Depends(get_metadata_registry),
) -> MemberPage:
    """Resolve prospective membership without persisting anything."""
    return await _dynamic_page(data.filters, data.page, data.page_size, client, registry)


@router.post("", response_model=CollectionView, status_code=201)
async def create_collection(
    data: CollectionCreate,
    db: Session = Depends(get_db),
    client: PaperlessClient = Depends(get_paperless_client),
    registry: MetadataRegistry = Depends(get_metadata_registry),
    owner_id: int = Depends(get_owner_id),
) -> CollectionView:
    ids = MemberIds(document_ids=data.document_ids).document_ids if data.document_ids else []
    if data.kind is CollectionKind.STATIC:
        await _check_visible(ids, client)
    else:
        assert data.filters is not None
        await _filter_params(data.filters, registry)
    row = Collection(
        name=data.name,
        description=data.description,
        kind=data.kind,
        owner_id=owner_id,
        filterset_json=data.filters.model_dump_json() if data.filters is not None else None,
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise PaperWrenchError(
            "A collection with this name already exists.",
            status_code=409,
            code=ErrorCode.CONFLICT,
        ) from exc
    db.add_all(CollectionDocument(collection_id=row.id, document_id=value) for value in ids)
    _commit(db)
    db.refresh(row)
    return await _view(db, row, client, registry)


@router.get("/{collection_id}", response_model=CollectionView)
async def get_collection(
    collection_id: int,
    db: Session = Depends(get_db),
    client: PaperlessClient = Depends(get_paperless_client),
    registry: MetadataRegistry = Depends(get_metadata_registry),
    owner_id: int = Depends(get_owner_id),
) -> CollectionView:
    return await _view(db, _row(db, collection_id, owner_id), client, registry)


@router.put("/{collection_id}", response_model=CollectionView)
async def update_collection(
    collection_id: int,
    data: CollectionUpdate,
    db: Session = Depends(get_db),
    client: PaperlessClient = Depends(get_paperless_client),
    registry: MetadataRegistry = Depends(get_metadata_registry),
    owner_id: int = Depends(get_owner_id),
) -> CollectionView:
    row = _row(db, collection_id, owner_id)
    if row.kind == CollectionKind.STATIC and data.filters is not None:
        raise PaperWrenchError(
            "A static collection cannot contain filters.",
            status_code=422,
            code=ErrorCode.VALIDATION_ERROR,
        )
    if row.kind == CollectionKind.DYNAMIC:
        filters = data.filters if data.filters is not None else _filters(row)
        await _filter_params(filters, registry)
        row.filterset_json = filters.model_dump_json()
    row.name, row.description, row.updated_at = data.name, data.description, utcnow()
    _commit(db)
    db.refresh(row)
    return await _view(db, row, client, registry)


@router.delete("/{collection_id}", status_code=204)
def delete_collection(
    collection_id: int,
    db: Session = Depends(get_db),
    owner_id: int = Depends(get_owner_id),
) -> Response:
    db.delete(_row(db, collection_id, owner_id))
    db.commit()
    return Response(status_code=204)


def _static_row(db: Session, collection_id: int, owner_id: int) -> Collection:
    row = _row(db, collection_id, owner_id)
    if row.kind != CollectionKind.STATIC:
        raise PaperWrenchError(
            "Dynamic collection membership is defined by its filters.",
            status_code=409,
            code=ErrorCode.CONFLICT,
        )
    return row


@router.post("/{collection_id}/documents", response_model=CollectionView)
async def add_members(
    collection_id: int,
    data: MemberIds,
    db: Session = Depends(get_db),
    client: PaperlessClient = Depends(get_paperless_client),
    registry: MetadataRegistry = Depends(get_metadata_registry),
    owner_id: int = Depends(get_owner_id),
) -> CollectionView:
    row = _static_row(db, collection_id, owner_id)
    existing = set(
        db.scalars(
            select(CollectionDocument.document_id).where(
                CollectionDocument.collection_id == collection_id,
                CollectionDocument.document_id.in_(data.document_ids),
            )
        ).all()
    )
    new_ids = [value for value in data.document_ids if value not in existing]
    await _check_visible(new_ids, client)
    db.add_all(
        CollectionDocument(collection_id=collection_id, document_id=value) for value in new_ids
    )
    row.updated_at = utcnow()
    _commit(db)
    return await _view(db, row, client, registry)


@router.delete("/{collection_id}/documents", response_model=CollectionView)
async def remove_members(
    collection_id: int,
    data: MemberIds,
    db: Session = Depends(get_db),
    client: PaperlessClient = Depends(get_paperless_client),
    registry: MetadataRegistry = Depends(get_metadata_registry),
    owner_id: int = Depends(get_owner_id),
) -> CollectionView:
    row = _static_row(db, collection_id, owner_id)
    db.execute(
        delete(CollectionDocument).where(
            CollectionDocument.collection_id == collection_id,
            CollectionDocument.document_id.in_(data.document_ids),
        )
    )
    row.updated_at = utcnow()
    db.commit()
    return await _view(db, row, client, registry)


@router.get("/{collection_id}/documents", response_model=MemberPage)
async def list_members(
    collection_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
    client: PaperlessClient = Depends(get_paperless_client),
    registry: MetadataRegistry = Depends(get_metadata_registry),
    owner_id: int = Depends(get_owner_id),
) -> MemberPage:
    row = _row(db, collection_id, owner_id)
    if row.kind == CollectionKind.DYNAMIC:
        return await _dynamic_page(_filters(row), page, page_size, client, registry)
    total = db.scalar(
        select(func.count()).select_from(CollectionDocument).where(
            CollectionDocument.collection_id == collection_id
        )
    ) or 0
    ids = db.scalars(
        select(CollectionDocument.document_id)
        .where(CollectionDocument.collection_id == collection_id)
        .order_by(CollectionDocument.document_id)
        .limit(page_size)
        .offset((page - 1) * page_size)
    ).all()
    items: list[MemberView] = []
    for member_id in ids:
        try:
            document = await client.get_document(member_id)
        except (PaperlessNotFoundError, PaperlessForbiddenError):
            items.append(MemberView(document_id=member_id, document=None, available=False))
            continue
        items.append(
            MemberView(
                document_id=member_id,
                document=await _document_to_list_item(document, registry),
                available=True,
            )
        )
    return MemberPage(
        items=items,
        page=page,
        page_size=page_size,
        total=total,
        page_count=(total + page_size - 1) // page_size,
    )
