"""Read-only inspection of normalized Paperless reference metadata.

Scope note (M2 point 8): this exposes the *data layer* only - the five
reference kinds the Metadata Registry knows about (tags, correspondents,
document types, storage paths, custom field definitions) - never Explorer
concepts like filters, saved views or bulk operations. Those belong to later
milestones (M3 Explorer, M4 Filter Engine) and must not leak in here.

Every payload is a PaperWrench model (:mod:`paperwrench.paperless.models`),
never a raw Paperless JSON passthrough - the frontend must not need to know
Paperless's field names or quirks.

Each request builds its own short-lived ``PaperlessClient`` and reads
straight through it, deliberately bypassing the in-process
:class:`~paperwrench.paperless.registry.MetadataRegistry` cache: there is no
long-lived registry instance shared across requests yet (that would require
an app-lifecycle-scoped singleton, which is out of scope for M2 and better
justified once a real consumer - e.g. the Filter Engine - needs the TTL
cache's benefit rather than the API responding to an unused endpoint).
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi import Depends

from paperwrench.api.deps import get_paperless_client
from paperwrench.paperless import Correspondent
from paperwrench.paperless import CustomField
from paperwrench.paperless import DocumentType
from paperwrench.paperless import PaperlessClient
from paperwrench.paperless import StoragePath
from paperwrench.paperless import Tag

router = APIRouter(prefix="/metadata", tags=["metadata"])


@router.get("/tags", response_model=list[Tag], summary="List normalized tags")
async def list_tags(client: PaperlessClient = Depends(get_paperless_client)) -> list[Tag]:
    return await client.list_tags()


@router.get(
    "/correspondents",
    response_model=list[Correspondent],
    summary="List normalized correspondents",
)
async def list_correspondents(
    client: PaperlessClient = Depends(get_paperless_client),
) -> list[Correspondent]:
    return await client.list_correspondents()


@router.get(
    "/document-types",
    response_model=list[DocumentType],
    summary="List normalized document types",
)
async def list_document_types(
    client: PaperlessClient = Depends(get_paperless_client),
) -> list[DocumentType]:
    return await client.list_document_types()


@router.get(
    "/storage-paths",
    response_model=list[StoragePath],
    summary="List normalized storage paths",
)
async def list_storage_paths(
    client: PaperlessClient = Depends(get_paperless_client),
) -> list[StoragePath]:
    return await client.list_storage_paths()


@router.get(
    "/custom-fields",
    response_model=list[CustomField],
    summary="List normalized custom field definitions",
)
async def list_custom_fields(
    client: PaperlessClient = Depends(get_paperless_client),
) -> list[CustomField]:
    return await client.list_custom_fields()
