"""Normalized single-document inspection and explicit manual edits."""

from typing import Any

from fastapi import APIRouter
from fastapi import Depends
from fastapi import Path
from pydantic import BaseModel

from paperwrench.api.deps import get_metadata_registry
from paperwrench.api.deps import get_paperless_client
from paperwrench.api.v1.documents import CustomFieldColumnValue
from paperwrench.api.v1.documents import DocumentListItem
from paperwrench.api.v1.documents import MetadataRef
from paperwrench.api.v1.documents import _document_to_list_item
from paperwrench.inspector import EDITABLE_CUSTOM_TYPES
from paperwrench.inspector import EditRequest
from paperwrench.inspector import catalog_revision
from paperwrench.inspector import edit_document
from paperwrench.paperless import CustomField
from paperwrench.paperless import Document
from paperwrench.paperless import MetadataRegistry
from paperwrench.paperless import PaperlessClient
from paperwrench.paperless.models import CustomFieldValueKind
from paperwrench.paperless.mutations import CorePatch
from paperwrench.paperless.mutations import revision
from paperwrench.paperless.registry import MetadataNotFoundError

router = APIRouter(prefix="/documents", tags=["inspector"])


class DocumentDetail(DocumentListItem):
    revision: str
    catalog_revision: str
    storage_path: MetadataRef | None
    original_file_name: str | None
    owner: int | None
    definitions: list[CustomField]
    editable_core_fields: list[str]
    editable_custom_types: list[str]


class EditResponse(BaseModel):
    before: DocumentDetail
    intended: dict[str, Any]
    document: DocumentDetail
    external_atomicity: bool = False
    durable_history: bool = False


async def document_detail(document: Document, registry: MetadataRegistry) -> DocumentDetail:
    item = await _document_to_list_item(document, registry)
    definitions = await registry.all_custom_fields()
    known = {field.id for field in definitions}
    # Unknown definitions remain visible and read-only, and survive every merge.
    for value in document.custom_fields:
        if value.field not in known:
            item.custom_fields.append(
                CustomFieldColumnValue(
                    field_id=value.field,
                    kind=CustomFieldValueKind.NULL
                    if value.value is None
                    else CustomFieldValueKind.PRESENT,
                    raw=value.value,
                )
            )
    storage_path = None
    if document.storage_path is not None:
        try:
            path = await registry.storage_path_by_id(document.storage_path)
            storage_path = MetadataRef(id=path.id, name=path.name)
        except MetadataNotFoundError:
            storage_path = MetadataRef(id=document.storage_path)
    return DocumentDetail(
        **item.model_dump(),
        revision=revision(document),
        catalog_revision=catalog_revision(definitions),
        storage_path=storage_path,
        original_file_name=document.original_file_name,
        owner=document.owner,
        definitions=definitions,
        editable_core_fields=list(CorePatch.model_fields),
        editable_custom_types=sorted(EDITABLE_CUSTOM_TYPES),
    )


@router.get("/{document_id}", response_model=DocumentDetail)
async def get_document(
    document_id: int = Path(gt=0),
    client: PaperlessClient = Depends(get_paperless_client),
    registry: MetadataRegistry = Depends(get_metadata_registry),
) -> DocumentDetail:
    return await document_detail(await client.get_document(document_id), registry)


@router.patch("/{document_id}", response_model=EditResponse)
async def patch_document(
    request: EditRequest,
    document_id: int = Path(gt=0),
    client: PaperlessClient = Depends(get_paperless_client),
    registry: MetadataRegistry = Depends(get_metadata_registry),
) -> EditResponse:
    result = await edit_document(document_id, request, client, registry)
    return EditResponse(
        before=await document_detail(result.before, registry),
        intended=result.intended,
        document=await document_detail(result.written, registry),
    )
