"""Permission, pagination, caching and drill-through contracts for #48."""

from __future__ import annotations

from datetime import UTC
from datetime import datetime

import respx
from fastapi.testclient import TestClient
from httpx import Request
from httpx import Response

BASE = "http://paperless.test"
HEADERS = {"X-Api-Version": "10", "X-Version": "3.2.1"}


def _page(results: list[dict[str, object]], *, count: int | None = None) -> dict[str, object]:
    return {
        "count": len(results) if count is None else count,
        "next": None,
        "previous": None,
        "results": results,
    }


def _document(document_id: int, *, days_ago: int, tag: int | None = 1) -> dict[str, object]:
    added = datetime.now(UTC).replace(microsecond=0)
    added = added.replace(day=max(1, added.day - days_ago))
    return {
        "id": document_id,
        "title": f"Document {document_id}",
        "correspondent": 7 if document_id == 1 else None,
        "document_type": 3,
        "storage_path": None,
        "tags": [tag] if tag is not None else [],
        "created": added.isoformat(),
        "modified": added.isoformat(),
        "added": added.isoformat(),
        "archive_serial_number": None,
        "original_file_name": None,
        "owner": 1,
        "custom_fields": [{"field": 11, "value": 0}] if document_id == 1 else [],
        "user_can_change": True,
        "deleted_at": None,
    }


@respx.mock
def test_dashboard_uses_bounded_pagination_exact_drilldowns_and_session_cache(
    client: TestClient,
) -> None:
    document_requests: list[Request] = []

    def documents(request: Request) -> Response:
        document_requests.append(request)
        if request.url.params["page_size"] == "1":
            return Response(200, json=_page([], count=42), headers=HEADERS)
        return Response(
            200,
            json=_page([_document(1, days_ago=0), _document(2, days_ago=1)], count=2),
            headers=HEADERS,
        )

    respx.get(f"{BASE}/api/documents/").mock(side_effect=documents)
    respx.get(f"{BASE}/api/tags/").mock(
        return_value=Response(200, json=_page([{"id": 1, "name": "Urgent"}]), headers=HEADERS)
    )
    respx.get(f"{BASE}/api/correspondents/").mock(
        return_value=Response(200, json=_page([{"id": 7, "name": "Hospital"}]), headers=HEADERS)
    )
    respx.get(f"{BASE}/api/document_types/").mock(
        return_value=Response(200, json=_page([{"id": 3, "name": "Invoice"}]), headers=HEADERS)
    )
    respx.get(f"{BASE}/api/custom_fields/").mock(
        return_value=Response(
            200,
            json=_page([{"id": 11, "name": "Amount", "data_type": "integer"}]),
            headers=HEADERS,
        )
    )

    first = client.get("/api/v1/analytics/dashboard?range=30d")
    second = client.get("/api/v1/analytics/dashboard?range=30d")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    assert len(document_requests) == 2  # count + one data page; second request is cached
    data_request = next(
        request for request in document_requests if request.url.params["page_size"] == "250"
    )
    assert "added__date__gte" in data_request.url.params
    assert "added__date__lt" in data_request.url.params

    payload = first.json()
    assert payload["total_visible"] == 42
    assert payload["documents_in_range"] == 2
    assert sum(point["count"] for point in payload["trend"]) == 2
    amount = payload["custom_fields"][0]
    assert (amount["present"], amount["missing"]) == (1, 1)
    missing_condition = amount["missing_query"]["filters"]["root"]["children"][-1]
    assert missing_condition["field"]["field_id"] == 11
    assert missing_condition["operator"] == "is_missing"


def test_dashboard_range_is_restricted_by_the_api_schema(client: TestClient) -> None:
    response = client.get("/api/v1/analytics/dashboard?range=all")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_dashboard_requires_authentication(unauthenticated_client: TestClient) -> None:
    response = unauthenticated_client.get("/api/v1/analytics/dashboard")
    assert response.status_code == 401
