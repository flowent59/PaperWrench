"""Authenticated encryption and bounded Argon2id password work (ADR-0019)."""

from __future__ import annotations

import asyncio
import json
import secrets
from collections import OrderedDict
from time import monotonic

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError
from argon2.exceptions import VerificationError
from cryptography.fernet import Fernet
from cryptography.fernet import InvalidToken

from paperwrench.config import Settings
from paperwrench.db.models import LocalCredential
from paperwrench.errors import ErrorCode
from paperwrench.errors import PaperWrenchError
from paperwrench.logging import register_secret


def invalid_credentials() -> PaperWrenchError:
    return PaperWrenchError(
        "Sign-in failed. Check your credentials or supply a new valid Paperless token.",
        status_code=401,
        code=ErrorCode.AUTH_INVALID_CREDENTIALS,
    )


class CredentialVault:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        key = settings.credential_key.get_secret_value()
        register_secret(key)
        self._cipher = Fernet(key.encode("ascii")) if key else None
        self._hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)
        self._dummy_hash = self._hasher.hash(secrets.token_urlsafe(32))
        self._work = asyncio.Semaphore(2)
        self._attempts: OrderedDict[str, tuple[float, int]] = OrderedDict()

    def require_available(self) -> None:
        if not self.settings.credential_storage_available:
            raise PaperWrenchError(
                "Remembered credentials are unavailable on this server.",
                status_code=503,
                code=ErrorCode.AUTH_STORAGE_UNAVAILABLE,
            )

    def throttle(self, peer: str) -> None:
        """Bound attempts per direct peer, without trusting forwarded headers."""
        now = monotonic()
        start, count = self._attempts.pop(peer, (now, 0))
        if now - start >= 60:
            start, count = now, 0
        self._attempts[peer] = (start, count + 1)
        if len(self._attempts) > 1024:
            self._attempts.popitem(last=False)
        if count >= 10:
            raise PaperWrenchError(
                "Too many sign-in attempts. Try again in a minute.",
                status_code=429,
                code=ErrorCode.AUTH_RATE_LIMITED,
            )

    async def hash_password(self, password: str) -> str:
        async with self._work:
            return await asyncio.to_thread(self._hasher.hash, password)

    async def verify_password(self, password_hash: str | None, password: str) -> bool:
        async with self._work:
            try:
                await asyncio.to_thread(
                    self._hasher.verify, password_hash or self._dummy_hash, password
                )
            except (VerificationError, InvalidHashError):
                return False
        return password_hash is not None

    def encrypt(self, owner_id: int, username: str, token: str) -> str:
        self.require_available()
        assert self._cipher is not None
        # Bind ciphertext to the identity and instance: copying ciphertext to
        # another row or changing PAPERLESS_URL cannot transfer its authority.
        payload = json.dumps([owner_id, username, self.settings.paperless_url, token])
        return self._cipher.encrypt(payload.encode("utf-8")).decode("ascii")

    def decrypt(self, credential: LocalCredential) -> str:
        self.require_available()
        assert self._cipher is not None
        try:
            payload = json.loads(self._cipher.decrypt(credential.encrypted_token.encode("ascii")))
            if (
                not isinstance(payload, list)
                or len(payload) != 4
                or payload[:3]
                != [credential.owner_id, credential.username, self.settings.paperless_url]
                or credential.paperless_url != self.settings.paperless_url
                or not isinstance(payload[3], str)
            ):
                raise invalid_credentials()
            token: str = payload[3]
            register_secret(token)
            return token
        except (InvalidToken, UnicodeError, ValueError):
            raise invalid_credentials() from None
