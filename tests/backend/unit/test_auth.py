"""Authentication sessions never disclose or persist Paperless credentials."""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from datetime import timedelta
from typing import cast

import httpx
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient

PROFILE = {
    "id": 42,
    "username": "alice",
    "first_name": "Alice",
    "last_name": "Example",
}


@respx.mock
def test_login_sets_http_only_cookie_and_never_returns_token(
    unauthenticated_client: TestClient,
) -> None:
    token = "alice-paperless-token-secret"
    respx.get("http://paperless.test/api/profile/").mock(
        return_value=httpx.Response(200, json=PROFILE)
    )

    response = unauthenticated_client.post("/api/v1/auth/login", json={"token": token})

    assert response.status_code == 200
    assert response.json()["username"] == "alice"
    assert token not in response.text
    assert "token" not in {key.lower() for key in response.json() if key != "csrf_token"}
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert "paperwrench_session" in cookie
    assert token not in cookie


@respx.mock
def test_login_normalizes_the_real_paperless_321_profile_without_disclosure(
    unauthenticated_client: TestClient,
) -> None:
    token = "paperless-321-profile-token-secret"
    respx.get("http://paperless.test/api/ui_settings/").respond(403)
    respx.get("http://paperless.test/api/profile/").mock(
        return_value=httpx.Response(
            200,
            json={
                "email": "alice@example.test",
                "first_name": "Alice",
                "last_name": "Example",
                "auth_token": token,
                "has_usable_password": True,
            },
        )
    )

    first = unauthenticated_client.post("/api/v1/auth/login", json={"token": token})
    second = unauthenticated_client.post("/api/v1/auth/login", json={"token": token})

    assert first.status_code == second.status_code == 200
    assert first.json()["user_id"] == second.json()["user_id"]
    assert first.json()["user_id"] > 0
    assert first.json()["username"] == "alice@example.test"
    assert first.json()["display_name"] == "Alice Example"
    assert token not in first.text


@respx.mock
def test_invalid_token_is_rejected_without_a_session(
    unauthenticated_client: TestClient,
) -> None:
    respx.get("http://paperless.test/api/profile/").mock(return_value=httpx.Response(401))
    response = unauthenticated_client.post(
        "/api/v1/auth/login", json={"token": "invalid-token-secret"}
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_INVALID_CREDENTIALS"
    assert "paperwrench_session" not in response.cookies


@respx.mock
def test_logout_revokes_session_and_requires_csrf(
    unauthenticated_client: TestClient,
) -> None:
    respx.get("http://paperless.test/api/profile/").mock(
        return_value=httpx.Response(200, json=PROFILE)
    )
    login = unauthenticated_client.post(
        "/api/v1/auth/login", json={"token": "logout-token-secret"}
    )
    csrf = login.json()["csrf_token"]

    rejected = unauthenticated_client.post("/api/v1/auth/logout")
    assert rejected.status_code == 403
    assert rejected.json()["error"]["code"] == "CSRF_FAILED"

    response = unauthenticated_client.post(
        "/api/v1/auth/logout", headers={"X-CSRF-Token": csrf}
    )
    assert response.status_code == 204
    assert unauthenticated_client.get("/api/v1/auth/me").status_code == 401


@respx.mock
def test_expired_session_is_removed(unauthenticated_client: TestClient) -> None:
    respx.get("http://paperless.test/api/profile/").mock(
        return_value=httpx.Response(200, json=PROFILE)
    )
    unauthenticated_client.post(
        "/api/v1/auth/login", json={"token": "expired-token-secret"}
    )
    session_id = unauthenticated_client.cookies["paperwrench_session"]
    app = cast(FastAPI, unauthenticated_client.app)
    record = app.state.sessions._sessions[session_id]
    record.expires_at = datetime.now(UTC) - timedelta(seconds=1)

    response = unauthenticated_client.get("/api/v1/auth/me")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_SESSION_EXPIRED"
    assert session_id not in app.state.sessions._sessions


@respx.mock
def test_revoked_token_invalidates_session(unauthenticated_client: TestClient) -> None:
    route = respx.get("http://paperless.test/api/profile/").mock(
        side_effect=[httpx.Response(200, json=PROFILE), httpx.Response(401)]
    )
    unauthenticated_client.post(
        "/api/v1/auth/login", json={"token": "revoked-token-secret"}
    )
    session_id = unauthenticated_client.cookies["paperwrench_session"]
    app = cast(FastAPI, unauthenticated_client.app)
    record = app.state.sessions._sessions[session_id]
    record.validated_at = datetime.now(UTC) - timedelta(hours=1)

    response = unauthenticated_client.get("/api/v1/auth/me")
    assert route.call_count == 2
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_SESSION_REVOKED"
    assert session_id not in app.state.sessions._sessions


def test_protected_api_requires_login(unauthenticated_client: TestClient) -> None:
    response = unauthenticated_client.get("/api/v1/collections")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_REQUIRED"


@respx.mock
def test_locale_is_created_updated_and_reused_per_paperless_user(
    unauthenticated_client: TestClient,
) -> None:
    respx.get("http://paperless.test/api/profile/").mock(
        return_value=httpx.Response(200, json=PROFILE)
    )

    login = unauthenticated_client.post(
        "/api/v1/auth/login",
        json={"token": "alice-locale-token", "locale": "fr"},
    )
    assert login.status_code == 200
    assert login.json()["locale"] == "fr"
    csrf = login.json()["csrf_token"]

    updated = unauthenticated_client.patch(
        "/api/v1/auth/preferences",
        json={"locale": "en"},
        headers={"X-CSRF-Token": csrf},
    )
    assert updated.status_code == 200
    assert updated.json() == {"locale": "en"}
    assert unauthenticated_client.get("/api/v1/auth/me").json()["locale"] == "en"

    assert unauthenticated_client.post(
        "/api/v1/auth/logout", headers={"X-CSRF-Token": csrf}
    ).status_code == 204
    second = unauthenticated_client.post(
        "/api/v1/auth/login",
        json={"token": "alice-new-token", "locale": "fr"},
    )
    assert second.status_code == 200
    assert second.json()["locale"] == "en"


@respx.mock
def test_locale_rejects_unsupported_values_and_requires_csrf(
    unauthenticated_client: TestClient,
) -> None:
    respx.get("http://paperless.test/api/profile/").mock(
        return_value=httpx.Response(200, json=PROFILE)
    )
    login = unauthenticated_client.post(
        "/api/v1/auth/login", json={"token": "alice-locale-token", "locale": "en"}
    )
    assert login.status_code == 200

    assert unauthenticated_client.patch(
        "/api/v1/auth/preferences", json={"locale": "fr"}
    ).status_code == 403
    invalid = unauthenticated_client.patch(
        "/api/v1/auth/preferences",
        json={"locale": "de"},
        headers={"X-CSRF-Token": login.json()["csrf_token"]},
    )
    assert invalid.status_code == 422
