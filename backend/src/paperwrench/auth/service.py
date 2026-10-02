"""Short-lived server-side sessions backed by a user's Paperless token.

The opaque cookie is only a lookup key. Sessions and their clients stay in
process memory. Optional encrypted credentials are managed separately; they
never restore sessions or resume durable work automatically.
"""

from __future__ import annotations

import asyncio
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from datetime import timedelta

from pydantic import SecretStr

from paperwrench.analytics import DashboardCache
from paperwrench.config import Settings
from paperwrench.errors import ErrorCode
from paperwrench.errors import PaperlessUnauthorizedError
from paperwrench.errors import PaperWrenchError
from paperwrench.logging import register_secret
from paperwrench.paperless import MetadataRegistry
from paperwrench.paperless import PaperlessClient

SESSION_COOKIE = "paperwrench_session"


@dataclass(slots=True)
class AuthSession:
    """Authenticated Paperless identity and its server-only client."""

    session_id: str
    paperless_user_id: int
    username: str
    display_name: str
    csrf_token: str
    expires_at: datetime
    validated_at: datetime
    client: PaperlessClient
    registry: MetadataRegistry
    dashboard_cache: DashboardCache
    identity_verified: bool = False
    upstream_user_id: int | None = None
    token_owner_id: int | None = None


class SessionStore:
    """Process-local credential vault with absolute expiry and revalidation."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._sessions: dict[str, AuthSession] = {}
        self._lock = asyncio.Lock()

    async def create(self, token: str) -> AuthSession:
        token = token.strip()
        if not token:
            raise PaperWrenchError(
                "Enter a Paperless API token.",
                status_code=401,
                code=ErrorCode.AUTH_INVALID_CREDENTIALS,
            )
        register_secret(token)
        session_settings = self.settings.model_copy(
            update={"paperless_token": SecretStr(token), "paperless_token_file": None}
        )
        client = PaperlessClient(session_settings)
        try:
            profile = await client.get_profile()
        except PaperlessUnauthorizedError as exc:
            await client.aclose()
            raise PaperWrenchError(
                "Paperless rejected this API token.",
                status_code=401,
                code=ErrorCode.AUTH_INVALID_CREDENTIALS,
            ) from exc
        except BaseException:
            await client.aclose()
            raise
        try:
            user_id = int(profile["id"])
            username = str(profile["username"])
        except (KeyError, TypeError, ValueError) as exc:
            await client.aclose()
            raise PaperWrenchError(
                "Paperless returned an unsupported profile response.",
                status_code=502,
                code=ErrorCode.PAPERLESS_INCOMPATIBLE,
            ) from exc
        first_name = str(profile.get("first_name") or "").strip()
        last_name = str(profile.get("last_name") or "").strip()
        now = datetime.now(UTC)
        record = AuthSession(
            session_id=secrets.token_urlsafe(32),
            paperless_user_id=user_id,
            username=username,
            display_name=" ".join(part for part in (first_name, last_name) if part) or username,
            csrf_token=secrets.token_urlsafe(32),
            expires_at=now + timedelta(seconds=self.settings.session_ttl_seconds),
            validated_at=now,
            client=client,
            registry=MetadataRegistry(client),
            dashboard_cache=DashboardCache(),
            identity_verified=profile.get("identity_verified") is True,
            upstream_user_id=profile.get("upstream_user_id"),
            token_owner_id=user_id,
        )
        async with self._lock:
            self._sessions[record.session_id] = record
        return record

    async def resolve(self, session_id: str | None) -> AuthSession:
        if not session_id:
            raise _auth_required()
        async with self._lock:
            record = self._sessions.get(session_id)
        if record is None:
            raise _auth_required()
        now = datetime.now(UTC)
        if record.expires_at <= now:
            await self.delete(session_id)
            raise PaperWrenchError(
                "Your PaperWrench session expired. Sign in again.",
                status_code=401,
                code=ErrorCode.AUTH_SESSION_EXPIRED,
            )
        if (now - record.validated_at).total_seconds() >= self.settings.session_revalidate_seconds:
            try:
                profile = await record.client.get_profile()
                if profile["id"] != (record.token_owner_id or record.paperless_user_id):
                    raise PaperlessUnauthorizedError("Paperless identity changed.")
                if (
                    record.upstream_user_id is not None
                    and profile.get("upstream_user_id") is not None
                    and profile["upstream_user_id"] != record.upstream_user_id
                ):
                    raise PaperlessUnauthorizedError("Paperless identity changed.")
            except PaperlessUnauthorizedError as exc:
                await self.delete(session_id)
                raise PaperWrenchError(
                    "Your Paperless token was revoked. Sign in again.",
                    status_code=401,
                    code=ErrorCode.AUTH_SESSION_REVOKED,
                ) from exc
            record.validated_at = now
        return record

    async def delete(self, session_id: str | None) -> None:
        if not session_id:
            return
        async with self._lock:
            record = self._sessions.pop(session_id, None)
        if record is not None:
            await record.client.invalidate_credentials()

    async def close(self) -> None:
        async with self._lock:
            records = list(self._sessions.values())
            self._sessions.clear()
        await asyncio.gather(*(record.client.invalidate_credentials() for record in records))

    async def delete_owner(self, owner_id: int, *, except_id: str | None = None) -> None:
        """Invalidate prior sessions after a remembered credential is changed."""
        async with self._lock:
            records = [
                self._sessions.pop(key)
                for key, value in list(self._sessions.items())
                if value.paperless_user_id == owner_id and key != except_id
            ]
        await asyncio.gather(*(record.client.invalidate_credentials() for record in records))

    async def cleanup_expired(self) -> None:
        """Destroy credentials whose absolute lifetime has elapsed."""
        now = datetime.now(UTC)
        async with self._lock:
            expired = [
                self._sessions.pop(session_id)
                for session_id, record in list(self._sessions.items())
                if record.expires_at <= now
            ]
        await asyncio.gather(*(record.client.invalidate_credentials() for record in expired))

    @staticmethod
    def verify_csrf(record: AuthSession, candidate: str | None) -> None:
        if not candidate or not hmac.compare_digest(record.csrf_token, candidate):
            raise PaperWrenchError(
                "The CSRF token is missing or invalid.",
                status_code=403,
                code=ErrorCode.CSRF_FAILED,
            )


def _auth_required() -> PaperWrenchError:
    return PaperWrenchError(
        "Authentication is required.",
        status_code=401,
        code=ErrorCode.AUTH_REQUIRED,
    )
