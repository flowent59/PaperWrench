"""Remembered credentials against disposable Paperless, never a real library."""

from pathlib import Path
from typing import cast

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pydantic import SecretStr

from paperwrench.config import Settings
from paperwrench.main import create_app

pytestmark = pytest.mark.live


async def test_regular_user_enrollment_restart_revocation_and_recovery(
    raw_live: httpx.AsyncClient,
    restricted_user: dict[str, object],
    live_settings: Settings,
    tmp_path: Path,
) -> None:
    user_id = cast(int, restricted_user["id"])
    username = cast(str, restricted_user["username"])
    token = cast(str, restricted_user["token"])
    settings = live_settings.model_copy(
        update={
            "database_url": f"sqlite+pysqlite:///{tmp_path / 'credentials.db'}",
            "credential_key": SecretStr(Fernet.generate_key().decode()),
            "session_revalidate_seconds": 0,
        }
    )
    password = "paperwrench-live-local-password"
    # No admin/user-list permissions: only the authenticated UI identity read.
    permitted = await raw_live.patch(
        f"/api/users/{user_id}/",
        json={
            "user_permissions": ["view_uisettings"],
        },
    )
    permitted.raise_for_status()
    with TestClient(create_app(settings)) as first:
        enrolled = first.post(
            "/api/v1/auth/login",
            json={
                "token": token,
                "remember": True,
                "password": password,
            },
        )
        assert enrolled.status_code == 200, enrolled.text
        assert enrolled.json()["username"] == username
        owner_id = enrolled.json()["user_id"]
        assert enrolled.json()["remembered"] is True
        assert token not in enrolled.text and password not in enrolled.text
        cookie = first.cookies["paperwrench_session"]
    with TestClient(create_app(settings)) as second:
        second.cookies.set("paperwrench_session", cookie)
        assert second.get("/api/v1/auth/me").status_code == 401
        second.cookies.clear()
        signed_in = second.post(
            "/api/v1/auth/password-login",
            json={
                "username": username,
                "password": password,
            },
        )
        assert signed_in.status_code == 200, signed_in.text
        assert signed_in.json()["user_id"] == owner_id
        # Revocation is observed both by existing sessions and password login.
        async with httpx.AsyncClient(
            base_url=live_settings.paperless_url,
            headers={"Authorization": f"Token {token}"},
            timeout=10.0,
        ) as upstream:
            rotated = await upstream.post("/api/profile/generate_auth_token/")
            rotated.raise_for_status()
            new_token = str(rotated.json())
        assert new_token != token
        assert second.get("/api/v1/auth/me").status_code == 401
        assert (
            second.post(
                "/api/v1/auth/password-login",
                json={
                    "username": username,
                    "password": password,
                },
            ).status_code
            == 401
        )
        recovered = second.post(
            "/api/v1/auth/login",
            json={
                "token": new_token,
                "remember": True,
                "password": "recovered-live-password",
            },
        )
        assert recovered.status_code == 200
        assert recovered.json()["user_id"] == owner_id
        assert (
            second.delete(
                "/api/v1/auth/credentials",
                headers={
                    "X-CSRF-Token": recovered.json()["csrf_token"],
                },
            ).status_code
            == 204
        )
        assert (
            second.post(
                "/api/v1/auth/password-login",
                json={
                    "username": username,
                    "password": "recovered-live-password",
                },
            ).status_code
            == 401
        )


async def test_without_identity_permission_token_login_still_works(
    restricted_user: dict[str, object],
    live_settings: Settings,
    tmp_path: Path,
) -> None:
    settings = live_settings.model_copy(
        update={
            "database_url": f"sqlite+pysqlite:///{tmp_path / 'restricted.db'}",
            "credential_key": SecretStr(Fernet.generate_key().decode()),
        }
    )
    token = cast(str, restricted_user["token"])
    with TestClient(create_app(settings)) as client:
        remembered = client.post(
            "/api/v1/auth/login",
            json={
                "token": token,
                "remember": True,
                "password": "local-live-password",
            },
        )
        assert remembered.status_code == 409
        assert remembered.json()["error"]["code"] == "AUTH_IDENTITY_UNSUPPORTED"
        ephemeral = client.post("/api/v1/auth/login", json={"token": token})
        assert ephemeral.status_code == 200
        assert ephemeral.json()["remembered"] is False
