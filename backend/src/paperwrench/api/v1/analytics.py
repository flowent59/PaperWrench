"""Read-only analytics endpoints."""

from fastapi import APIRouter
from fastapi import Depends

from paperwrench.analytics import DashboardCache
from paperwrench.analytics import DashboardRange
from paperwrench.analytics import DashboardSnapshot
from paperwrench.analytics import build_dashboard
from paperwrench.api.deps import get_dashboard_cache
from paperwrench.api.deps import get_metadata_registry
from paperwrench.api.deps import get_paperless_client
from paperwrench.paperless import MetadataRegistry
from paperwrench.paperless import PaperlessClient

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/dashboard", response_model=DashboardSnapshot)
async def dashboard(
    range: DashboardRange = DashboardRange.DAYS_30,
    client: PaperlessClient = Depends(get_paperless_client),
    registry: MetadataRegistry = Depends(get_metadata_registry),
    cache: DashboardCache = Depends(get_dashboard_cache),
) -> DashboardSnapshot:
    return await cache.get_or_build(
        range,
        lambda: build_dashboard(
            client,
            registry,
            range,
            cache_ttl_seconds=cache.ttl_seconds,
        ),
    )
