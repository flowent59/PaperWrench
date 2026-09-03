"""System endpoints: health, instance info and the Paperless connection probe."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from paperwrench import __version__
from paperwrench.config import SUPPORTED_PAPERLESS_API_VERSION
from paperwrench.config import Settings
from paperwrench.config import get_settings
from paperwrench.db.session import get_db
from paperwrench.logging import get_logger
from paperwrench.paperless import ConnectionStatus
from paperwrench.paperless import PaperlessClient

logger = get_logger(__name__)

router = APIRouter(prefix="/system", tags=["system"])


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    database: Literal["ok", "error"]


class InfoResponse(BaseModel):
    """Non-secret instance information.

    Deliberately exposes booleans and versions only - never the Paperless URL
    credentials or token (ADR-0002).
    """

    version: str
    paperless_configured: bool
    paperless_api_version: int
    supported_paperless_api_version: int
    default_page_size: int
    max_concurrency: int


@router.get("/health", response_model=HealthResponse, summary="Liveness and DB check")
def health(db: Session = Depends(get_db)) -> HealthResponse:
    database: Literal["ok", "error"] = "ok"
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("health_db_check_failed", error=str(exc))
        database = "error"

    return HealthResponse(
        status="ok" if database == "ok" else "degraded",
        version=__version__,
        database=database,
    )


@router.get("/info", response_model=InfoResponse, summary="Non-secret instance info")
def info(settings: Settings = Depends(get_settings)) -> InfoResponse:
    return InfoResponse(
        version=__version__,
        paperless_configured=settings.paperless_configured,
        paperless_api_version=settings.paperless_api_version,
        supported_paperless_api_version=SUPPORTED_PAPERLESS_API_VERSION,
        default_page_size=settings.default_page_size,
        max_concurrency=settings.max_concurrency,
    )


@router.get(
    "/paperless",
    response_model=ConnectionStatus,
    summary="Paperless connection and API compatibility",
)
async def paperless_status(settings: Settings = Depends(get_settings)) -> ConnectionStatus:
    """Probe the configured Paperless instance.

    Always answers ``200``: an unreachable or incompatible Paperless is a
    *state* the UI has to render, not a failure of this endpoint. The details
    are in the body.

    The response carries no credential - see
    :class:`~paperwrench.paperless.models.ConnectionStatus`.
    """
    async with PaperlessClient(settings) as client:
        status = await client.check_connection()

    logger.info(
        "paperless_probe",
        configured=status.configured,
        connected=status.connected,
        compatible=status.compatible,
        api_version=status.api_version,
        paperless_version=status.paperless_version,
        error_code=status.error_code,
    )
    return status
