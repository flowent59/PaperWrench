"""Login/logout API for per-user Paperless tokens."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter
from fastapi import Depends
from fastapi import Request
from fastapi import Response
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from paperwrench.api.deps import get_auth_session
from paperwrench.auth.credentials import CredentialVault
from paperwrench.auth.credentials import invalid_credentials
from paperwrench.auth.service import SESSION_COOKIE
from paperwrench.auth.service import AuthSession
from paperwrench.config import Settings
from paperwrench.config import get_settings
from paperwrench.db.models import LocalCredential
from paperwrench.db.models import PaperlessIdentity
from paperwrench.db.models import UserPreference
from paperwrench.db.session import get_db
from paperwrench.errors import ErrorCode
from paperwrench.errors import PaperWrenchError

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: SecretStr = Field(min_length=1, max_length=4096)
    locale: Literal["en", "fr"] = "en"
    remember: bool = False  # Legacy API callers retain ephemeral behavior.
    password: SecretStr | None = Field(default=None, min_length=12, max_length=1024)


class PasswordLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=255)
    password: SecretStr = Field(min_length=1, max_length=1024)


class CredentialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: SecretStr = Field(min_length=1, max_length=4096)
    password: SecretStr = Field(min_length=12, max_length=1024)


class SessionView(BaseModel):
    user_id: int
    username: str
    display_name: str
    expires_at: datetime
    csrf_token: str
    locale: Literal["en", "fr"] | None
    remembered: bool


class LocalePreference(BaseModel):
    locale: Literal["en", "fr"]


def _stored_locale(db: Session, owner_id: int) -> Literal["en", "fr"] | None:
    preference = db.get(UserPreference, owner_id)
    if preference is None:
        return None
    return "fr" if preference.locale == "fr" else "en"


def _view(record: AuthSession, db: Session) -> SessionView:
    return SessionView(
        user_id=record.paperless_user_id,
        username=record.username,
        display_name=record.display_name,
        expires_at=record.expires_at,
        csrf_token=record.csrf_token,
        locale=_stored_locale(db, record.paperless_user_id),
        remembered=db.get(LocalCredential, record.paperless_user_id) is not None,
    )


def _vault(request: Request) -> CredentialVault:
    vault: CredentialVault = request.app.state.credential_vault
    return vault


def _throttle(request: Request) -> None:
    _vault(request).throttle(request.client.host if request.client else "unknown")


def _bind_owner(db: Session, record: AuthSession, settings: Settings) -> None:
    """Preserve legacy ownership and recognize the same identity after rotation.

    No data is copied or merged. The first verified login binds its existing
    owner key; later credentials for that upstream identity reuse that key.
    The non-secret binding survives deletion of the remembered credential.
    """
    if not record.identity_verified or record.upstream_user_id is None:
        return
    binding = db.scalar(
        select(PaperlessIdentity).where(
            PaperlessIdentity.upstream_user_id == record.upstream_user_id,
            PaperlessIdentity.paperless_url == settings.paperless_url,
        )
    )
    if binding is not None:
        record.paperless_user_id = binding.owner_id
        return
    if db.get(PaperlessIdentity, record.paperless_user_id) is not None:
        raise PaperWrenchError(
            "This owner belongs to another Paperless identity or instance.",
            status_code=409,
            code=ErrorCode.AUTH_IDENTITY_CONFLICT,
        )
    db.add(
        PaperlessIdentity(
            owner_id=record.paperless_user_id,
            upstream_user_id=record.upstream_user_id,
            paperless_url=settings.paperless_url,
        )
    )
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise PaperWrenchError(
            "Identity enrollment changed concurrently. Try again.",
            status_code=409,
            code=ErrorCode.AUTH_IDENTITY_CONFLICT,
        ) from None


def _save_credential(
    db: Session, record: AuthSession, token: str, password_hash: str, vault: CredentialVault
) -> None:
    if not record.identity_verified:
        raise PaperWrenchError(
            "Paperless must return a stable user ID and username to remember a token.",
            status_code=409,
            code=ErrorCode.AUTH_IDENTITY_UNSUPPORTED,
        )
    credential = db.get(LocalCredential, record.paperless_user_id)
    if credential is not None and credential.paperless_url != vault.settings.paperless_url:
        raise PaperWrenchError(
            "This account belongs to another Paperless instance.",
            status_code=409,
            code=ErrorCode.AUTH_IDENTITY_CONFLICT,
        )
    if credential is None:
        credential = LocalCredential(owner_id=record.paperless_user_id)
        db.add(credential)
    credential.username = record.username
    credential.paperless_url = vault.settings.paperless_url
    credential.password_hash = password_hash
    credential.encrypted_token = vault.encrypt(record.paperless_user_id, record.username, token)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise PaperWrenchError(
            "This username is already bound to another Paperless identity.",
            status_code=409,
            code=ErrorCode.AUTH_IDENTITY_CONFLICT,
        ) from None


def _cookie(response: Response, request: Request, settings: Settings, record: AuthSession) -> None:
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


@router.get("/options")
def options(request: Request, response: Response) -> dict[str, bool]:
    response.headers["Cache-Control"] = "no-store"
    return {"remember_available": _vault(request).settings.credential_storage_available}


@router.post("/login", response_model=SessionView)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
) -> SessionView:
    _throttle(request)
    if not settings.paperless_login_configured:
        raise PaperWrenchError(
            "Paperless is not configured: set PAPERLESS_URL.",
            status_code=503,
            code=ErrorCode.PAPERLESS_NOT_CONFIGURED,
        )
    vault = _vault(request)
    if body.remember:
        vault.require_available()
        if body.password is None:
            raise PaperWrenchError(
                "A local password is required to remember a token.",
                status_code=422,
                code=ErrorCode.VALIDATION_ERROR,
            )
    token = body.token.get_secret_value().strip()
    record = await request.app.state.sessions.create(token)
    try:
        password_hash = None
        if body.remember and body.password is not None:
            password_hash = await vault.hash_password(body.password.get_secret_value())
        _bind_owner(db, record, settings)
        if password_hash is not None:
            _save_credential(db, record, token, password_hash, vault)
        if _stored_locale(db, record.paperless_user_id) is None:
            db.add(UserPreference(owner_id=record.paperless_user_id, locale=body.locale))
        db.commit()
    except BaseException:
        db.rollback()
        await request.app.state.sessions.delete(record.session_id)
        raise
    if body.remember:
        await request.app.state.sessions.delete_owner(
            record.paperless_user_id, except_id=record.session_id
        )
    await request.app.state.sessions.delete(request.cookies.get(SESSION_COOKIE))
    _cookie(response, request, settings, record)
    return _view(record, db)


@router.post("/password-login", response_model=SessionView)
async def password_login(
    body: PasswordLoginRequest,
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
) -> SessionView:
    _throttle(request)
    vault = _vault(request)
    vault.require_available()
    credential = db.scalar(select(LocalCredential).where(LocalCredential.username == body.username))
    password_hash = credential.password_hash if credential else None
    if not await vault.verify_password(password_hash, body.password.get_secret_value()):
        raise invalid_credentials()
    assert credential is not None
    ciphertext = credential.encrypted_token
    owner_id = credential.owner_id
    token = vault.decrypt(credential)
    db.rollback()  # Do not hold a SQLite read transaction during upstream I/O.
    record = await request.app.state.sessions.create(token)
    try:
        db.expire_all()
        current = db.get(LocalCredential, owner_id)
        binding = db.get(PaperlessIdentity, owner_id)
        if (
            current is None
            or current.password_hash != password_hash
            or current.encrypted_token != ciphertext
            or not record.identity_verified
            or binding is None
            or binding.upstream_user_id != record.upstream_user_id
            or binding.paperless_url != settings.paperless_url
            or record.username != body.username
        ):
            raise invalid_credentials()
        record.paperless_user_id = owner_id
    except BaseException:
        await request.app.state.sessions.delete(record.session_id)
        raise
    await request.app.state.sessions.delete(request.cookies.get(SESSION_COOKIE))
    _cookie(response, request, settings, record)
    return _view(record, db)


@router.put("/credentials", response_model=SessionView)
async def replace_credentials(
    body: CredentialRequest,
    request: Request,
    response: Response,
    record: AuthSession = Depends(get_auth_session),
    db: Session = Depends(get_db),
) -> SessionView:
    _throttle(request)
    vault = _vault(request)
    vault.require_available()
    token = body.token.get_secret_value().strip()
    candidate = await request.app.state.sessions.create(token)
    try:
        if (
            candidate.upstream_user_id is None
            or candidate.upstream_user_id != record.upstream_user_id
        ):
            raise PaperWrenchError(
                "A token for the same Paperless identity is required.",
                status_code=409,
                code=ErrorCode.AUTH_IDENTITY_CONFLICT,
            )
        password_hash = await vault.hash_password(body.password.get_secret_value())
        _bind_owner(db, candidate, vault.settings)
        if candidate.paperless_user_id != record.paperless_user_id:
            raise invalid_credentials()
        _save_credential(db, candidate, token, password_hash, vault)
        db.commit()
    except BaseException:
        db.rollback()
        await request.app.state.sessions.delete(candidate.session_id)
        raise
    await request.app.state.sessions.delete_owner(
        record.paperless_user_id, except_id=candidate.session_id
    )
    _cookie(response, request, vault.settings, candidate)
    return _view(candidate, db)


@router.delete("/credentials", status_code=204)
async def delete_credentials(
    request: Request,
    response: Response,
    record: AuthSession = Depends(get_auth_session),
    db: Session = Depends(get_db),
) -> None:
    credential = db.get(LocalCredential, record.paperless_user_id)
    if credential is not None:
        db.delete(credential)
        db.commit()
    await request.app.state.sessions.delete_owner(record.paperless_user_id)
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, samesite="strict")
    response.headers["Cache-Control"] = "no-store"


@router.get("/me", response_model=SessionView)
async def me(
    response: Response,
    record: AuthSession = Depends(get_auth_session),
    db: Session = Depends(get_db),
) -> SessionView:
    response.headers["Cache-Control"] = "no-store"
    return _view(record, db)


@router.patch("/preferences", response_model=LocalePreference)
async def update_preferences(
    body: LocalePreference,
    record: AuthSession = Depends(get_auth_session),
    db: Session = Depends(get_db),
) -> LocalePreference:
    preference = db.get(UserPreference, record.paperless_user_id)
    if preference is None:
        preference = UserPreference(owner_id=record.paperless_user_id, locale=body.locale)
        db.add(preference)
    else:
        preference.locale = body.locale
    db.commit()
    return LocalePreference(locale=body.locale)


@router.post("/logout", status_code=204)
async def logout(
    request: Request,
    response: Response,
    record: AuthSession = Depends(get_auth_session),
) -> None:
    await request.app.state.sessions.delete(record.session_id)
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, samesite="strict")
    response.headers["Cache-Control"] = "no-store"
