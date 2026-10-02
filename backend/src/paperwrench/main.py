"""FastAPI application factory.

Serves both the JSON API and the built SPA from a single origin, which is why
CORS is disabled by default (ADR-0002).
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from paperwrench import __version__
from paperwrench.api.v1 import api_router
from paperwrench.auth import SessionStore
from paperwrench.auth.credentials import CredentialVault
from paperwrench.config import Settings
from paperwrench.config import get_settings
from paperwrench.db.engine import dispose_engine
from paperwrench.db.engine import init_engine
from paperwrench.db.lock import HEARTBEAT_INTERVAL_SECONDS
from paperwrench.db.lock import acquire_lock
from paperwrench.db.lock import build_instance_id
from paperwrench.db.lock import refresh_lock
from paperwrench.db.lock import release_lock
from paperwrench.db.migrate import run_migrations
from paperwrench.db.session import session_scope
from paperwrench.errors import ErrorCode
from paperwrench.errors import ErrorDetail
from paperwrench.errors import ErrorResponse
from paperwrench.errors import PaperWrenchError
from paperwrench.jobs.engine import JobEngine
from paperwrench.jobs.engine import recover
from paperwrench.logging import configure_logging
from paperwrench.logging import get_logger
from paperwrench.logging import register_secret
from paperwrench.previews.service import PreviewService
from paperwrench.previews.service import cleanup as cleanup_previews
from paperwrench.schedules.engine import ScheduleEngine

logger = get_logger(__name__)

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

_HTTP_ERROR_CODES = {
    400: ErrorCode.VALIDATION_ERROR,
    403: ErrorCode.FORBIDDEN_ORIGIN,
    404: ErrorCode.NOT_FOUND,
    405: ErrorCode.NOT_FOUND,
    422: ErrorCode.VALIDATION_ERROR,
}


async def _heartbeat_loop(instance_id: str, jobs: JobEngine, sessions: SessionStore) -> None:
    """Keep the runtime lock fresh so a crash is detectable."""
    while True:  # pragma: no cover - background task
        await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
        try:
            await sessions.cleanup_expired()
            cleanup_previews()
            with session_scope() as session:
                if not refresh_lock(session, instance_id):
                    logger.error("runtime_lock_lost", instance_id=instance_id)
                    jobs.stop_scheduling()
                    return
        except Exception as exc:
            jobs.stop_scheduling()
            logger.warning("runtime_lock_heartbeat_failed", error=str(exc))
            return


class OriginGuardMiddleware(BaseHTTPMiddleware):
    """Reject cross-origin state-changing requests.

    MVP PaperWrench has no cookie-based session, so classic CSRF does not
    apply - but this keeps it that way even if a browser sends an
    unexpected cross-site mutation. See ADR-0002.
    """

    def __init__(self, app: Any, allowed_origins: list[str]) -> None:
        super().__init__(app)
        self._allowed = set(allowed_origins)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.method not in SAFE_METHODS:
            origin = request.headers.get("origin")
            if origin and origin not in self._allowed:
                host = request.headers.get("host", "")
                if not host or origin != f"{request.url.scheme}://{host}":
                    return JSONResponse(
                        status_code=403,
                        content=ErrorResponse(
                            error=ErrorDetail(
                                code=ErrorCode.FORBIDDEN_ORIGIN,
                                message="Cross-origin state-changing request rejected.",
                            )
                        ).model_dump(mode="json"),
                    )
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Conservative security headers for a self-hosted app."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if request.url.path.startswith("/api/v1/auth/"):
            response.headers["Cache-Control"] = "no-store"
        return response


def _register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(PaperWrenchError)
    async def _handle_paperwrench_error(_request: Request, exc: PaperWrenchError) -> JSONResponse:
        logger.warning("api_error", code=str(exc.code), message=exc.message)
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.to_response().model_dump(mode="json"),
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=PaperWrenchError(
                "Request validation failed.",
                code=ErrorCode.VALIDATION_ERROR,
                # Omit input/context and scrub paths too: an unknown key is user input.
                details={
                    "errors": [
                        {"loc": list(error["loc"]), "type": error["type"], "msg": error["msg"]}
                        for error in exc.errors()
                    ]
                },
            )
            .to_response()
            .model_dump(mode="json"),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_error(_request: Request, exc: StarletteHTTPException) -> Response:
        # Routing/method errors must use the same envelope as domain errors so
        # the frontend only ever needs one error parser.
        code = _HTTP_ERROR_CODES.get(exc.status_code, ErrorCode.INTERNAL_ERROR)
        return JSONResponse(
            status_code=exc.status_code,
            headers=getattr(exc, "headers", None),
            content=ErrorResponse(error=ErrorDetail(code=code, message=str(exc.detail))).model_dump(
                mode="json"
            ),
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(_request: Request, exc: Exception) -> JSONResponse:
        # Never leak internals (which could include a URL or header) to the client.
        logger.exception("unhandled_exception", error=str(exc))
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                error=ErrorDetail(
                    code=ErrorCode.INTERNAL_ERROR,
                    message="An unexpected internal error occurred.",
                )
            ).model_dump(mode="json"),
        )


def _mount_spa(app: FastAPI, static_dir: Path) -> None:
    """Serve the built SPA, falling back to index.html for client routes."""
    assets = static_dir / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    index_file = static_dir / "index.html"

    # Deliberately a sync endpoint: it stats the filesystem, so FastAPI runs it
    # in the threadpool instead of blocking the event loop (ruff ASYNC240).
    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str) -> Response:
        # The catch-all must never swallow an unmatched API route: a client
        # expecting JSON would receive the HTML shell with a 200 and fail in a
        # way that is very hard to diagnose.
        if full_path == "api" or full_path.startswith("api/"):
            raise StarletteHTTPException(status_code=404, detail="Not Found")

        candidate = static_dir / full_path
        if (
            full_path
            and candidate.is_file()
            and candidate.resolve().is_relative_to(static_dir.resolve())
        ):
            return FileResponse(candidate)
        if index_file.is_file():
            return FileResponse(index_file)
        return JSONResponse(
            status_code=404,
            content=ErrorResponse(
                error=ErrorDetail(
                    code=ErrorCode.NOT_FOUND,
                    message="Frontend build not found. Run the Vite dev server or build the SPA.",
                )
            ).model_dump(mode="json"),
        )


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application."""
    settings = settings or get_settings()
    configure_logging(level=settings.log_level, fmt=settings.log_format)
    register_secret(settings.paperless_token.get_secret_value())

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Schema first: the runtime lock lives in a migrated table.
        run_migrations(settings.database_url)
        init_engine(settings.database_url)
        instance_id = build_instance_id()
        force = os.getenv("PAPERWRENCH_FORCE_LOCK", "").lower() in {"1", "true", "yes"}

        with session_scope() as session:
            acquire_lock(session, instance_id, force=force)

        # Each login owns a server-side Paperless client and metadata cache.
        # No deployment-wide credential is constructed or required.
        app.state.sessions = SessionStore(settings)
        app.state.credential_vault = CredentialVault(settings)
        cleanup_previews(startup=True)
        app.state.previews = PreviewService()
        recover()
        app.state.jobs = JobEngine(None, None, settings, instance_id)
        app.state.jobs.start()
        app.state.schedules = ScheduleEngine(app.state.jobs, app.state.sessions, app.state.previews)
        app.state.schedules.start()

        heartbeat = asyncio.create_task(
            _heartbeat_loop(instance_id, app.state.jobs, app.state.sessions)
        )
        logger.info(
            "paperwrench_started",
            version=__version__,
            instance_id=instance_id,
            paperless_configured=settings.paperless_configured,
            paperless_api_version=settings.paperless_api_version,
        )
        try:
            yield
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            await app.state.schedules.close()
            await app.state.jobs.close()
            await app.state.sessions.close()
            try:
                with session_scope() as session:
                    release_lock(session, instance_id)
            except Exception as exc:  # pragma: no cover - shutdown best effort
                logger.warning("runtime_lock_release_failed", error=str(exc))
            dispose_engine()
            logger.info("paperwrench_stopped")

    app = FastAPI(
        title="PaperWrench API",
        description=(
            "Power tools for Paperless-ngx. PaperWrench is an independent project and is "
            "not officially affiliated with Paperless-ngx."
        ),
        version=__version__,
        lifespan=lifespan,
        openapi_url="/api/openapi.json",
        docs_url="/api/docs",
        redoc_url=None,
    )

    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(OriginGuardMiddleware, allowed_origins=settings.cors_origins)
    if settings.cors_origins:
        # Split-origin development only; empty in production.
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # The factory's settings win over the process-wide cached ones, otherwise
    # tests (and any embedded use) would silently read the ambient environment.
    app.dependency_overrides[get_settings] = lambda: settings

    _register_exception_handlers(app)
    app.include_router(api_router)

    static_dir = settings.resolved_static_dir
    if static_dir is not None:
        _mount_spa(app, static_dir)
    else:
        logger.info("spa_not_mounted", reason="no built frontend found")

    return app


app = create_app()
