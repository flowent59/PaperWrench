"""M7 staging/confirmation only. No Apply or Job route exists here."""

from fastapi import APIRouter
from fastapi import Depends
from fastapi import Query
from fastapi import Request
from fastapi import Response

from paperwrench.api.deps import get_metadata_registry
from paperwrench.api.deps import get_paperless_client
from paperwrench.paperless import MetadataRegistry
from paperwrench.paperless import PaperlessClient
from paperwrench.previews.model import ConfirmPreview
from paperwrench.previews.model import CreatedPreview
from paperwrench.previews.model import PreviewPage
from paperwrench.previews.model import PreviewSummary
from paperwrench.previews.service import PreviewService
from paperwrench.transformations.model import ResultStatus
from paperwrench.transformations.model import Transformation


def no_cache(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(prefix="/previews", tags=["previews"], dependencies=[Depends(no_cache)])


def get_previews(request: Request) -> PreviewService:
    service: PreviewService = request.app.state.previews
    return service


@router.post("", response_model=CreatedPreview, status_code=201)
async def create_preview(
    transformation: Transformation,
    client: PaperlessClient = Depends(get_paperless_client),
    registry: MetadataRegistry = Depends(get_metadata_registry),
    service: PreviewService = Depends(get_previews),
) -> CreatedPreview:
    return await service.create(transformation, client, registry)


@router.get("/{preview_id}", response_model=PreviewSummary)
def preview_summary(
    preview_id: str, service: PreviewService = Depends(get_previews)
) -> PreviewSummary:
    return service.summary(preview_id)


@router.get("/{preview_id}/documents", response_model=PreviewPage)
def preview_documents(
    preview_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = 100,
    status: ResultStatus | None = None,
    service: PreviewService = Depends(get_previews),
) -> PreviewPage:
    return service.page(preview_id, page, page_size, status)


@router.post("/{preview_id}/confirm", response_model=PreviewSummary)
def confirm_preview(
    preview_id: str,
    confirmation: ConfirmPreview,
    service: PreviewService = Depends(get_previews),
) -> PreviewSummary:
    """Record review once. Does not create a Job or execute any change."""
    return service.confirm(preview_id, confirmation)


@router.delete("/{preview_id}", status_code=204)
def discard_preview(preview_id: str, service: PreviewService = Depends(get_previews)) -> None:
    service.discard(preview_id)
