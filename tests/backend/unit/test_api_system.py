"""Contract tests for the system endpoints exposed in M0.

These endpoints are the first thing the frontend talks to, so their shape is
part of the public API contract from now on.
"""

from __future__ import annotations

import pytest
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


@pytest.mark.parametrize("origin", ["https://testserver", "null", "https://evil://testserver"])
def test_origin_guard_compares_the_complete_origin(client: TestClient, origin: str) -> None:
    response = client.post("/api/v1/collections", json={"name": "blocked", "document_ids": []},
                           headers={"Origin": origin})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN_ORIGIN"
    assert client.get("/api/v1/collections").json() == []


def test_same_origin_mutation_is_allowed(client: TestClient) -> None:
    response = client.post("/api/v1/collections", json={"name": "allowed", "document_ids": []},
                           headers={"Origin": "http://testserver"})
    assert response.status_code == 201


@pytest.mark.parametrize("host", ["127.0.0.1:8787", "192.168.1.42:8787", "localhost:8787"])
def test_lan_http_same_origin_mutation_is_allowed(client: TestClient, host: str) -> None:
    response = client.post(
        "/api/v1/collections",
        json={"name": "lan", "document_ids": []},
        headers={"Host": host, "Origin": f"http://{host}"},
    )
    assert response.status_code == 201


@pytest.mark.parametrize(
    "origin", ["https://192.168.1.42:8787", "http://192.168.1.42:8788", "null"]
)
def test_lan_http_cross_origin_mutation_is_rejected(client: TestClient, origin: str) -> None:
    response = client.post(
        "/api/v1/collections",
        json={"name": "blocked", "document_ids": []},
        headers={"Host": "192.168.1.42:8787", "Origin": origin},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN_ORIGIN"
