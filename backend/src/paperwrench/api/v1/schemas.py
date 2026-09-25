"""PaperWrench-owned schema CRUD and bounded read-only evaluation."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi import Depends
from fastapi import Query
from fastapi import Response
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from paperwrench.api.deps import get_metadata_registry
from paperwrench.api.deps import get_paperless_client
from paperwrench.api.v1.documents import build_query_params
from paperwrench.db.base import utcnow
from paperwrench.db.models import DocumentSchema
from paperwrench.db.session import get_db
from paperwrench.errors import ErrorCode
from paperwrench.errors import PaperWrenchError
from paperwrench.filters import DatasetPageRequest
from paperwrench.filters import build_catalog
from paperwrench.paperless import MetadataRegistry
from paperwrench.paperless import PaperlessClient
from paperwrench.schemas.model import SchemaDefinition
from paperwrench.schemas.model import SchemaEvaluationPage
from paperwrench.schemas.model import SchemaView
from paperwrench.schemas.model import StoredRules
from paperwrench.schemas.model import StoredScope
from paperwrench.schemas.service import evaluate_document
from paperwrench.schemas.service import validate_definition
from paperwrench.schemas.service import validate_rules

router = APIRouter(prefix="/schemas", tags=["schemas"])


def _missing() -> PaperWrenchError:
    return PaperWrenchError("Schema not found.", status_code=404, code=ErrorCode.NOT_FOUND)


def _row(db: Session, schema_id: int) -> DocumentSchema:
    row = db.get(DocumentSchema, schema_id)
    if row is None:
        raise _missing()
    return row


def _definition(row: DocumentSchema) -> SchemaDefinition:
    try:
        scope = StoredScope.model_validate_json(row.applies_when_json)
        rules = StoredRules.model_validate_json(row.rules_json)
    except ValidationError as exc:
        raise PaperWrenchError(
            "Stored schema contract is invalid; edit or recreate this schema.",
            status_code=422,
            code=ErrorCode.VALIDATION_ERROR,
        ) from exc
    return SchemaDefinition(
        name=row.name,
        description=row.description,
        applies_when=scope.query,
        rules=rules.items,
    )


def _view(row: DocumentSchema) -> SchemaView:
    return SchemaView(
        id=row.id,
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
        **_definition(row).model_dump(),
    )


async def _validate(schema: SchemaDefinition, registry: MetadataRegistry) -> None:
    catalog = await build_catalog(registry)
    validate_definition(schema, catalog)
    # The shared dataset path validates search, ordering and full FilterSet
    # compilability. Failure stops before any document list request.
    await build_query_params(
        DatasetPageRequest(**schema.applies_when.model_dump(exclude_none=True), page_size=25),
        registry=registry,
    )


def _save(row: DocumentSchema, schema: SchemaDefinition, db: Session) -> None:
    row.name = schema.name.strip()
    row.description = schema.description
    row.applies_when_json = StoredScope(query=schema.applies_when).model_dump_json()
    row.rules_json = StoredRules(items=schema.rules).model_dump_json()
    row.updated_at = utcnow()
    db.add(row)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise PaperWrenchError(
            "A schema with this name already exists.", status_code=409, code=ErrorCode.CONFLICT
        ) from exc
    db.refresh(row)


@router.get("", response_model=list[SchemaView])
def list_schemas(db: Session = Depends(get_db)) -> list[SchemaView]:
    rows = db.scalars(select(DocumentSchema).order_by(DocumentSchema.name)).all()
    return [_view(row) for row in rows]


@router.post("", response_model=SchemaView, status_code=201)
async def create_schema(
    schema: SchemaDefinition,
    registry: MetadataRegistry = Depends(get_metadata_registry),
    db: Session = Depends(get_db),
) -> SchemaView:
    await _validate(schema, registry)
    row = DocumentSchema()
    _save(row, schema, db)
    return _view(row)


@router.get("/{schema_id}", response_model=SchemaView)
def get_schema(schema_id: int, db: Session = Depends(get_db)) -> SchemaView:
    return _view(_row(db, schema_id))


@router.put("/{schema_id}", response_model=SchemaView)
async def update_schema(
    schema_id: int,
    schema: SchemaDefinition,
    registry: MetadataRegistry = Depends(get_metadata_registry),
    db: Session = Depends(get_db),
) -> SchemaView:
    row = _row(db, schema_id)
    await _validate(schema, registry)
    _save(row, schema, db)
    return _view(row)


@router.delete("/{schema_id}", status_code=204)
def delete_schema(schema_id: int, db: Session = Depends(get_db)) -> Response:
    row = _row(db, schema_id)
    db.delete(row)
    db.commit()
    return Response(status_code=204)


@router.get("/{schema_id}/evaluate", response_model=SchemaEvaluationPage)
async def evaluate_schema(
    schema_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
    client: PaperlessClient = Depends(get_paperless_client),
    registry: MetadataRegistry = Depends(get_metadata_registry),
) -> SchemaEvaluationPage:
    schema = _definition(_row(db, schema_id))
    definitions = {field.id: field for field in await registry.all_custom_fields()}
    validate_rules(schema.rules, await build_catalog(registry))
    request = DatasetPageRequest(
        **schema.applies_when.model_dump(exclude_none=True), page=page, page_size=page_size
    )
    params = await build_query_params(
        request,
        registry=registry,
    )
    batch = await client.list_documents(params=params, page=page, page_size=page_size)
    return SchemaEvaluationPage(
        schema_id=schema_id,
        items=[evaluate_document(schema, document, definitions) for document in batch.results],
        page=page,
        page_size=page_size,
        total=batch.count,
        page_count=(batch.count + page_size - 1) // page_size,
    )
