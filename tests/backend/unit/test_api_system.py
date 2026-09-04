"""Contract tests for the system endpoints exposed in M0.

These endpoints are the first thing the frontend talks to, so their shape is
part of the public API contract from now on.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from paperwrench import __version__


def test_health_reports_ok_with_a_reachable_database(client: TestClient) -> None:
    response = client.get("/api/v1/system/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["database"] == "ok"
    assert payload["version"] == __version__


def test_info_exposes_configuration_without_secrets(client: TestClient) -> None:
    response = client.get("/api/v1/system/info")

    assert response.status_code == 200
    payload = response.json()

    assert payload["version"] == __version__
    assert payload["paperless_api_version"] == 10
    assert payload["paperless_configured"] is True
    assert payload["supported_paperless_api_version"] == 10
    assert payload["max_concurrency"] >= 1

    # The token, and any hint of it, must never reach the browser.
    serialised = response.text.lower()
    for forbidden in ("token", "secret", "password", "paperless.test"):
        assert forbidden not in serialised


def test_unknown_api_route_returns_the_uniform_error_envelope(client: TestClient) -> None:
    response = client.get("/api/v1/system/does-not-exist")

    assert response.status_code == 404
    body = response.json()
    assert "error" in body
    assert body["error"]["code"]
    assert body["error"]["message"]


def test_security_headers_are_applied(client: TestClient) -> None:
    response = client.get("/api/v1/system/health")

    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "SAMEORIGIN"
    assert "referrer-policy" in response.headers


def test_openapi_schema_is_generated(client: TestClient) -> None:
    response = client.get("/api/openapi.json")

    assert response.status_code == 200
    schema = response.json()
    assert "/api/v1/system/health" in schema["paths"]
    assert "/api/v1/system/info" in schema["paths"]
