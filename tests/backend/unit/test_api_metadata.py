"""Contract tests for the internal metadata inspection endpoints (M2 point 8).

These are unit tests against the FastAPI app with ``respx`` mocking the
Paperless side - they prove PaperWrench's own endpoint shape and payload
model, not that Paperless's shapes are real (see tests/backend/live for
that).
"""

from __future__ import annotations

import respx
from fastapi.testclient import TestClient
from httpx import Response

BASE = "http://paperless.test"

V10_HEADERS = {"X-Api-Version": "10", "X-Version": "3.1.2"}


def _page(results: list[dict[str, object]]) -> dict[str, object]:
    return {"count": len(results), "next": None, "previous": None, "results": results}


@respx.mock
def test_list_tags_returns_normalized_paperwrench_models(client: TestClient) -> None:
    respx.get(f"{BASE}/api/tags/").mock(
        return_value=Response(
            200,
            json=_page([{"id": 1, "name": "Facture", "slug": "facture", "color": "#ff0000"}]),
            headers=V10_HEADERS,
        )
    )

    response = client.get("/api/v1/metadata/tags")

    assert response.status_code == 200
    payload = response.json()
    assert payload == [
        {
            "id": 1,
            "name": "Facture",
            "slug": "facture",
            "color": "#ff0000",
            "is_inbox_tag": None,
            "owner": None,
            "user_can_change": None,
            "document_count": None,
        }
    ]


@respx.mock
def test_list_correspondents_returns_normalized_paperwrench_models(
    client: TestClient,
) -> None:
    respx.get(f"{BASE}/api/correspondents/").mock(
        return_value=Response(
            200,
            json=_page([{"id": 7, "name": "Hopital Saint-Joseph", "slug": "hopital"}]),
            headers=V10_HEADERS,
        )
    )

    response = client.get("/api/v1/metadata/correspondents")

    assert response.status_code == 200
    assert response.json()[0]["id"] == 7
    assert response.json()[0]["name"] == "Hopital Saint-Joseph"


@respx.mock
def test_list_document_types_returns_normalized_paperwrench_models(
    client: TestClient,
) -> None:
    respx.get(f"{BASE}/api/document_types/").mock(
        return_value=Response(
            200, json=_page([{"id": 3, "name": "Facture", "slug": "facture"}]), headers=V10_HEADERS
        )
    )

    response = client.get("/api/v1/metadata/document-types")

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": 3,
            "name": "Facture",
            "slug": "facture",
            "owner": None,
            "user_can_change": None,
            "document_count": None,
        }
    ]


@respx.mock
def test_list_storage_paths_returns_normalized_paperwrench_models(
    client: TestClient,
) -> None:
    respx.get(f"{BASE}/api/storage_paths/").mock(
        return_value=Response(
            200,
            json=_page([{"id": 4, "name": "Archives", "slug": "archives", "path": "archives/"}]),
            headers=V10_HEADERS,
        )
    )

    response = client.get("/api/v1/metadata/storage-paths")

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": 4,
            "name": "Archives",
            "slug": "archives",
            "path": "archives/",
            "owner": None,
            "user_can_change": None,
            "document_count": None,
        }
    ]


@respx.mock
def test_list_custom_fields_returns_normalized_paperwrench_models(
    client: TestClient,
) -> None:
    respx.get(f"{BASE}/api/custom_fields/").mock(
        return_value=Response(
            200,
            json=_page(
                [{"id": 9, "name": "Montant", "data_type": "monetary", "extra_data": {}}]
            ),
            headers=V10_HEADERS,
        )
    )

    response = client.get("/api/v1/metadata/custom-fields")

    assert response.status_code == 200
    assert response.json() == [
        {"id": 9, "name": "Montant", "data_type": "monetary", "extra_data": {}}
    ]


@respx.mock
def test_metadata_endpoints_never_leak_the_paperless_token(client: TestClient) -> None:
    respx.get(f"{BASE}/api/tags/").mock(
        return_value=Response(500, json={"detail": "boom"}, headers=V10_HEADERS)
    )

    response = client.get("/api/v1/metadata/tags")

    assert "test-token-abcdef123456" not in response.text


def test_metadata_router_is_registered_in_the_openapi_schema(client: TestClient) -> None:
    response = client.get("/api/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    for path in (
        "/api/v1/metadata/tags",
        "/api/v1/metadata/correspondents",
        "/api/v1/metadata/document-types",
        "/api/v1/metadata/storage-paths",
        "/api/v1/metadata/custom-fields",
    ):
        assert path in paths, f"{path} missing from the OpenAPI schema"
