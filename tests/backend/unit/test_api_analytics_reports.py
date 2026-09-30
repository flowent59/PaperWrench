"""Custom-field report and authorized CSV contracts for #49."""

from __future__ import annotations

from datetime import UTC
from datetime import datetime

import respx
from fastapi.testclient import TestClient
from httpx import Request
from httpx import Response

BASE = "http://paperless.test"
HEADERS = {"X-Api-Version": "10", "X-Version": "3.2.1"}


def _page(results: list[dict[str, object]]) -> dict[str, object]:
    return {"count": len(results), "next": None, "previous": None, "results": results}


def _document(document_id: int, value: object = "absent") -> dict[str, object]:
    now = datetime.now(UTC).replace(microsecond=0).isoformat()
    fields = [] if value == "absent" else [{"field": 11, "value": value}]
    return {
        "id": document_id,
        "title": f"Document {document_id}",
        "correspondent": 7,
        "document_type": 3,
        "storage_path": None,
        "tags": [],
        "created": now,
        "modified": now,
        "added": now,
        "archive_serial_number": None,
        "original_file_name": None,
        "owner": 1,
        "custom_fields": fields,
        "user_can_change": True,
        "deleted_at": None,
    }


def _mock_metadata(field: dict[str, object] | None = None) -> None:
    respx.get(f"{BASE}/api/custom_fields/").mock(
        return_value=Response(
            200,
            json=_page([field or {"id": 11, "name": "Amount", "data_type": "float"}]),
            headers=HEADERS,
        )
    )
    respx.get(f"{BASE}/api/document_types/").mock(
        return_value=Response(200, json=_page([{"id": 3, "name": "Invoice"}]), headers=HEADERS)
    )
    respx.get(f"{BASE}/api/correspondents/").mock(
        return_value=Response(200, json=_page([{"id": 7, "name": "Hospital"}]), headers=HEADERS)
    )


@respx.mock
def test_numeric_report_preserves_filters_zero_missing_states_and_permissions(
    client: TestClient,
) -> None:
    _mock_metadata()
    route = respx.get(f"{BASE}/api/documents/").mock(
        return_value=Response(
            200,
            json=_page(
                [
                    _document(1, 0),
                    _document(2, 2.5),
                    _document(3, None),
                    _document(4),
                    _document(5, "not-a-number"),
                ]
            ),
            headers=HEADERS,
        )
    )
    body = {
        "field_id": 11,
        "range": "30d",
        "group_by": "month",
        "numeric_semantics": "sum",
        "filters": {
            "root": {
                "kind": "group",
                "operator": "and",
                "children": [
                    {
                        "kind": "condition",
                        "field": {"source": "core", "name": "correspondent"},
                        "operator": "equals",
                        "value": 7,
                    }
                ],
            }
        },
    }

    response = client.post("/api/v1/analytics/reports", json=body)

    assert response.status_code == 200
    request: Request = route.calls.last.request
    assert request.headers["authorization"] == "Token test-token-abcdef123456"
    assert request.url.params["correspondent__id"] == "7"
    assert request.url.params["page_size"] == "250"
    assert "added__date__gte" in request.url.params
    payload = response.json()
    assert payload["aggregation"] == "sum"
    assert payload["additive"] is True
    assert payload["valued_documents"] == 2  # zero is a real value
    assert payload["groups"][0]["values"] == [{"value": "2.5", "currency": None}]
    assert {item["kind"]: item["count"] for item in payload["missing"]} == {
        "absent": 1,
        "null": 1,
        "invalid": 1,
    }
    drill_children = payload["groups"][0]["query"]["filters"]["root"]["children"]
    assert any(item["field"].get("name") == "correspondent" for item in drill_children)
    added_conditions = [item for item in drill_children if item["field"].get("name") == "added"]
    assert [item["operator"] for item in added_conditions] == [
        "greater_or_equal",
        "less_than",
    ]


@respx.mock
def test_snapshot_report_uses_latest_value_and_is_explicitly_non_additive(
    client: TestClient,
) -> None:
    _mock_metadata()
    first = _document(1, 10)
    first["added"] = "2026-09-01T10:00:00Z"
    second = _document(2, 20)
    second["added"] = "2026-09-02T10:00:00Z"
    respx.get(f"{BASE}/api/documents/").mock(
        return_value=Response(200, json=_page([first, second]), headers=HEADERS)
    )

    response = client.post(
        "/api/v1/analytics/reports",
        json={
            "field_id": 11,
            "range": "365d",
            "group_by": "document_type",
            "numeric_semantics": "snapshot",
        },
    )

    assert response.status_code == 200
    assert response.json()["aggregation"] == "latest_snapshot"
    assert response.json()["additive"] is False
    assert response.json()["groups"][0]["values"][0]["value"] == "20"


@respx.mock
def test_csv_export_contains_only_report_rows_and_neutralizes_formulas(
    client: TestClient,
) -> None:
    _mock_metadata({"id": 11, "name": "=Amount", "data_type": "integer"})
    respx.get(f"{BASE}/api/documents/").mock(
        return_value=Response(200, json=_page([_document(1, 4)]), headers=HEADERS)
    )

    response = client.post(
        "/api/v1/analytics/reports/export",
        json={"field_id": 11, "group_by": "correspondent"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment;" in response.headers["content-disposition"]
    assert "'=Amount" in response.text
    assert "test-token" not in response.text


@respx.mock
def test_date_report_groups_by_the_custom_field_month(client: TestClient) -> None:
    _mock_metadata({"id": 11, "name": "Due date", "data_type": "date"})
    respx.get(f"{BASE}/api/documents/").mock(
        return_value=Response(
            200,
            json=_page([_document(1, "2026-04-03"), _document(2, "2026-04-20")]),
            headers=HEADERS,
        )
    )

    response = client.post(
        "/api/v1/analytics/reports",
        json={"field_id": 11, "group_by": "month", "range": "365d"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["aggregation"] == "count"
    assert payload["groups"][0]["key"] == "2026-04"
    assert payload["groups"][0]["document_count"] == 2
