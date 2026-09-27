"""Login/logout API for per-user Paperless tokens."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter
from fastapi import Depends
from fastapi import Request
from fastapi import Response
from pydantic import BaseModel
from pydantic import SecretStr

from paperwrench.api.deps import get_auth_session
from paperwrench.auth.service import SESSION_COOKIE
from paperwrench.auth.service import AuthSession
from paperwrench.config import Settings
from paperwrench.config import get_settings
from paperwrench.errors import ErrorCode
from paperwrench.errors import PaperWrenchError

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    token: SecretStr


class SessionView(BaseModel):
    user_id: int
    username: str
    display_name: str
    expires_at: datetime
    csrf_token: str


def _view(record: AuthSession) -> SessionView:
    return SessionView(
        user_id=record.paperless_user_id,
        username=record.username,
        display_name=record.display_name,
        expires_at=record.expires_at,
        csrf_token=record.csrf_token,
    )


@router.post("/login", response_model=SessionView)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings),
) -> SessionView:
    if not settings.paperless_login_configured:
        raise PaperWrenchError(
            "Paperless is not configured: set PAPERLESS_URL.",
            status_code=503,
            code=ErrorCode.PAPERLESS_NOT_CONFIGURED,
        )
    record = await request.app.state.sessions.create(body.token.get_secret_value())
    secure = settings.session_cookie_secure
    if secure is None:
        secure = request.url.scheme == "https"
    response.set_cookie(
        SESSION_COOKIE,
        record.session_id,
        max_age=settings.session_ttl_seconds,
        expires=record.expires_at,
        httponly=True,
        secure=secure,
        samesite="strict",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return _view(record)


@router.get("/me", response_model=SessionView)
async def me(
    response: Response,
    record: AuthSession = Depends(get_auth_session),
) -> SessionView:
    response.headers["Cache-Control"] = "no-store"
    return _view(record)


@router.post("/logout", status_code=204)
async def logout(
    request: Request,
    response: Response,
    record: AuthSession = Depends(get_auth_session),
) -> None:
    await request.app.state.sessions.delete(record.session_id)
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, samesite="strict")
    response.headers["Cache-Control"] = "no-store"
