"""Explicit Apply and durable paginated History."""

from fastapi import APIRouter
from fastapi import Depends
from fastapi import Query
from fastapi import Request
from fastapi import Response

from paperwrench.api.deps import get_metadata_registry
from paperwrench.api.deps import get_owner_id
from paperwrench.api.deps import get_paperless_client
from paperwrench.api.v1.previews import get_previews
from paperwrench.db.models import TargetStatus
from paperwrench.jobs import store
from paperwrench.jobs.engine import JobEngine
from paperwrench.jobs.model import CreateJob
from paperwrench.jobs.model import CreateRollback
from paperwrench.jobs.model import HistoryPage
from paperwrench.jobs.model import JobView
from paperwrench.jobs.model import OperationView
from paperwrench.jobs.model import TargetView
from paperwrench.jobs.rollback import create_rollback
from paperwrench.paperless import MetadataRegistry
from paperwrench.paperless import PaperlessClient
from paperwrench.previews.model import CreatedPreview
from paperwrench.previews.service import PreviewService


def no_cache(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(prefix="/jobs", tags=["jobs"], dependencies=[Depends(no_cache)])


def engine(request: Request) -> JobEngine:
    result: JobEngine = request.app.state.jobs
    return result


@router.post("", response_model=JobView, status_code=201)
async def apply_preview(
    body: CreateJob,
    worker: JobEngine = Depends(engine),
    client: PaperlessClient = Depends(get_paperless_client),
    registry: MetadataRegistry = Depends(get_metadata_registry),
    owner_id: int = Depends(get_owner_id),
) -> JobView:
    worker.check_available()
    job_id = store.create_job(body, owner_id)
    worker.bind(job_id, owner_id, client, registry)
    worker.wake.set()
    return store.job_view(job_id, owner_id)


@router.get("", response_model=HistoryPage[JobView])
def jobs(
    page: int = Query(1, ge=1),
    page_size: int = 25,
    owner_id: int = Depends(get_owner_id),
) -> HistoryPage[JobView]:
    return store.job_page(page, page_size, owner_id)


@router.get("/{job_id}", response_model=JobView)
def job(job_id: int, owner_id: int = Depends(get_owner_id)) -> JobView:
    return store.job_view(job_id, owner_id)


@router.get("/{job_id}/targets", response_model=HistoryPage[TargetView])
def targets(
    job_id: int,
    page: int = Query(1, ge=1),
    page_size: int = 25,
    status: TargetStatus | None = None,
    owner_id: int = Depends(get_owner_id),
) -> HistoryPage[TargetView]:
    return store.target_page(job_id, page, page_size, status, owner_id)


@router.get("/{job_id}/operations", response_model=HistoryPage[OperationView])
def operations(
    job_id: int,
    page: int = Query(1, ge=1),
    page_size: int = 25,
    document_id: int | None = Query(None, gt=0),
    owner_id: int = Depends(get_owner_id),
) -> HistoryPage[OperationView]:
    return store.operation_page(job_id, page, page_size, document_id, owner_id)


@router.post("/{job_id}/resume", response_model=JobView)
async def resume(
    job_id: int,
    worker: JobEngine = Depends(engine),
    client: PaperlessClient = Depends(get_paperless_client),
    registry: MetadataRegistry = Depends(get_metadata_registry),
    owner_id: int = Depends(get_owner_id),
) -> JobView:
    worker.bind(job_id, owner_id, client, registry)
    worker.resume(job_id, owner_id)
    return store.job_view(job_id, owner_id)


@router.post("/{job_id}/rollback-preview", response_model=CreatedPreview, status_code=201)
async def rollback_preview(
    job_id: int,
    worker: JobEngine = Depends(engine),
    previews: PreviewService = Depends(get_previews),
    client: PaperlessClient = Depends(get_paperless_client),
    registry: MetadataRegistry = Depends(get_metadata_registry),
    owner_id: int = Depends(get_owner_id),
) -> CreatedPreview:
    return await previews.create(
        None,
        client,
        registry,
        rollback_of_job_id=job_id,
        owner_id=owner_id,
    )


@router.post("/{job_id}/rollback", response_model=JobView, status_code=201)
async def rollback(
    job_id: int,
    body: CreateRollback,
    worker: JobEngine = Depends(engine),
    client: PaperlessClient = Depends(get_paperless_client),
    registry: MetadataRegistry = Depends(get_metadata_registry),
    owner_id: int = Depends(get_owner_id),
) -> JobView:
    worker.check_available()
    rollback_id = create_rollback(job_id, body, owner_id)
    worker.bind(rollback_id, owner_id, client, registry)
    worker.wake.set()
    return store.job_view(rollback_id, owner_id)
