"""Authentication-aware dependencies shared by API endpoints.

Each server-side login session owns one Paperless client and metadata cache.
FastAPI caches these dependency results within a request, while the session
store keeps them isolated across users.
"""

from __future__ import annotations

from fastapi import Depends
from fastapi import Request

from paperwrench.analytics import DashboardCache
from paperwrench.auth.service import SESSION_COOKIE
from paperwrench.auth.service import AuthSession
from paperwrench.paperless import MetadataRegistry
from paperwrench.paperless import PaperlessClient


async def get_auth_session(request: Request) -> AuthSession:
    """Resolve and periodically revalidate the opaque browser session."""
    store = request.app.state.sessions
    record: AuthSession = await store.resolve(request.cookies.get(SESSION_COOKIE))
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        store.verify_csrf(record, request.headers.get("x-csrf-token"))
    request.state.auth = record
    return record


async def require_session(record: AuthSession = Depends(get_auth_session)) -> None:
    """Router-level dependency used to protect the application API."""
    _ = record


async def get_owner_id(record: AuthSession = Depends(get_auth_session)) -> int:
    """Stable Paperless user ID used by every local ownership boundary."""
    return record.paperless_user_id


async def get_paperless_client(
    record: AuthSession = Depends(get_auth_session),
) -> PaperlessClient:
    """The current user's server-side Paperless client."""
    return record.client


async def get_metadata_registry(
    record: AuthSession = Depends(get_auth_session),
) -> MetadataRegistry:
    """Metadata cache scoped to the authenticated Paperless account."""
    return record.registry


async def get_dashboard_cache(
    record: AuthSession = Depends(get_auth_session),
) -> DashboardCache:
    """Dashboard snapshot cache isolated to the authenticated session."""
    return record.dashboard_cache
