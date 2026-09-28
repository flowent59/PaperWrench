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
