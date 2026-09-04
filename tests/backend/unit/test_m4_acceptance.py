"""The M4 acceptance scenario, walked step by step.

This is the brief's own target scenario expressed as one test, in order, with
each step named. It duplicates coverage that exists in the compiler and API
suites on purpose: those prove individual behaviours, this proves the
sequence a user actually performs still hangs together - in particular that
the count, the grid total and the compiled query all agree at every step,
and that the two refusals (an unsupported mixed OR, any fetch-all) hold at
the end of a long interaction rather than only in isolation.

Paperless is mocked here, so this proves what PaperWrench *sends* and *does*.
The same scenario against a real 3.1.2 lives in
``tests/backend/live/test_paperless_live.py`` (TestFilterEngine*Live).
"""

from __future__ import annotations

import json
from typing import Any

import respx
from fastapi.testclient import TestClient
from httpx import Request
from httpx import Response

BASE = "http://paperless.test"
V10 = {"X-Api-Version": "10", "X-Version": "3.1.2"}

DOCUMENT_TYPE_ID = 3  # "Relevé de vacations"
MONTANT = 2
PERIODE = 1


def _page(results: list[dict[str, Any]], count: int | None = None) -> dict[str, Any]:
    return {
        "count": count if count is not None else len(results),
        "next": None,
        "previous": None,
        "results": results,
    }


def _document(document_id: int, title: str) -> dict[str, Any]:
    return {
        "id": document_id,
        "title": title,
        "correspondent": None,
        "document_type": DOCUMENT_TYPE_ID,
        "tags": [],
        "created": "2024-06-01",
        "modified": "2024-06-02T00:00:00Z",
        "added": "2024-06-01T00:00:00Z",
        "archive_serial_number": None,
        "custom_fields": [],
        "user_can_change": True,
    }


def _condition(source: str, key: Any, operator: str, value: Any = None) -> dict[str, Any]:
    field = (
        {"source": "core", "name": key}
        if source == "core"
        else {"source": "custom_field", "field_id": key}
    )
    return {"kind": "condition", "field": field, "operator": operator, "value": value}


def _filters(*children: dict[str, Any], operator: str = "and") -> dict[str, Any]:
    return {"root": {"kind": "group", "operator": operator, "children": list(children)}}


@respx.mock
def test_the_m4_acceptance_scenario(client: TestClient) -> None:
    for endpoint in ("tags", "correspondents", "storage_paths"):
        respx.get(f"{BASE}/api/{endpoint}/").mock(
            return_value=Response(200, json=_page([]), headers=V10)
        )
    respx.get(f"{BASE}/api/document_types/").mock(
        return_value=Response(
            200,
            json=_page([{"id": DOCUMENT_TYPE_ID, "name": "Relevé de vacations"}]),
            headers=V10,
        )
    )
    respx.get(f"{BASE}/api/custom_fields/").mock(
        return_value=Response(
            200,
            json=_page(
                [
                    {
                        "id": PERIODE,
                        "name": "Période concernée",
                        "data_type": "string",
                        "extra_data": None,
                    },
                    {
                        "id": MONTANT,
                        "name": "Montant",
                        "data_type": "monetary",
                        "extra_data": {"default_currency": "EUR"},
                    },
                ]
            ),
            headers=V10,
        )
    )
    documents = respx.get(f"{BASE}/api/documents/").mock(
        return_value=Response(
            200,
            json=_page([_document(11, "scan_20241001_0051")], count=1),
            headers=V10,
        )
    )
    writes = respx.route(
        method__in=["PATCH", "POST", "PUT", "DELETE"], host="paperless.test"
    ).mock(return_value=Response(500, json={"detail": "should never be called"}))

    def last_params() -> dict[str, str]:
        request: Request = documents.calls.last.request
        return dict(request.url.params)

    # -- 1. Open the Explorer ------------------------------------------------
    # The field list it offers comes from live metadata, not a constant.
    capabilities = client.get("/api/v1/filters/capabilities").json()
    labels = {field["label"] for field in capabilities["fields"]}
    assert {"Montant", "Période concernée", "Document type"} <= labels

    # -- 2. Choose the document type "Relevé de vacations" -------------------
    step2 = _filters(_condition("core", "document_type", "equals", DOCUMENT_TYPE_ID))
    assert client.post("/api/v1/filters/validate", json={"filters": step2}).json() == {
        "valid": True,
        "compilable": True,
        "issues": [],
        "compiled": {
            "params": {"document_type__id": "3"},
            "custom_field_expression": None,
        },
    }

    # -- 3./4. Add "Montant is missing" and "Période concernée is not missing"
    step4 = _filters(
        _condition("core", "document_type", "equals", DOCUMENT_TYPE_ID),
        _condition("custom_field", MONTANT, "is_missing"),
        _condition("custom_field", PERIODE, "is_present"),
    )
    validation = client.post("/api/v1/filters/validate", json={"filters": step4}).json()
    assert validation["valid"] and validation["compilable"]
    assert validation["compiled"]["custom_field_expression"] == [
        "AND",
        [[MONTANT, "exists", False], [PERIODE, "exists", True]],
    ]

    # -- 5. The Explorer displays only the matching documents ----------------
    page = client.post(
        "/api/v1/documents/query", json={"filters": step4, "page_size": 25}
    )
    assert page.status_code == 200
    assert last_params()["document_type__id"] == "3"
    assert json.loads(last_params()["custom_field_query"]) == [
        "AND",
        [[MONTANT, "exists", False], [PERIODE, "exists", True]],
    ]

    # -- 6. The Filter Engine's count matches the Explorer's total -----------
    count = client.post("/api/v1/filters/count", json={"filters": step4}).json()
    assert count["count"] == page.json()["total"]
    # And it was obtained without fetching the matches.
    assert last_params()["page_size"] == "1"

    # -- 7. Change the condition to "Montant > EUR0.00" ----------------------
    # A zero amount is a real value; this must exclude it as well as ABSENT.
    step7 = _filters(
        _condition("core", "document_type", "equals", DOCUMENT_TYPE_ID),
        _condition("custom_field", MONTANT, "greater_than", "EUR0.00"),
    )
    compiled = client.post("/api/v1/filters/validate", json={"filters": step7}).json()
    assert compiled["compiled"]["custom_field_expression"] == [MONTANT, "gt", "0.00"]

    # -- 8. Results update server-side ---------------------------------------
    before = documents.call_count
    client.post("/api/v1/documents/query", json={"filters": step7, "page_size": 25})
    assert documents.call_count == before + 1, "exactly one request per page shown"
    assert json.loads(last_params()["custom_field_query"]) == [MONTANT, "gt", "0.00"]

    # -- 9. Combine supported conditions with AND ----------------------------
    step9 = _filters(
        _condition("core", "document_type", "equals", DOCUMENT_TYPE_ID),
        _condition("core", "title", "contains", "scan"),
        _condition("custom_field", MONTANT, "greater_than", "EUR0.00"),
        _condition("custom_field", PERIODE, "is_present"),
    )
    client.post("/api/v1/documents/query", json={"filters": step9, "page_size": 25})
    params = last_params()
    assert params["document_type__id"] == "3"
    assert params["title__icontains"] == "scan"
    assert json.loads(params["custom_field_query"]) == [
        "AND",
        [[MONTANT, "gt", "0.00"], [PERIODE, "exists", True]],
    ]

    # -- 10. Add a supported OR group over custom fields ---------------------
    step10 = _filters(
        _condition("core", "document_type", "equals", DOCUMENT_TYPE_ID),
        {
            "kind": "group",
            "operator": "or",
            "children": [
                _condition("custom_field", MONTANT, "is_missing"),
                _condition("custom_field", PERIODE, "is_missing"),
            ],
        },
    )
    client.post("/api/v1/documents/query", json={"filters": step10, "page_size": 25})
    assert json.loads(last_params()["custom_field_query"]) == [
        "OR",
        [[MONTANT, "exists", False], [PERIODE, "exists", False]],
    ]

    # -- 11./12. Attempt an unsupported mixed core/custom OR; it is refused ---
    calls_before_refusal = documents.call_count
    step11 = _filters(
        _condition("core", "document_type", "equals", DOCUMENT_TYPE_ID),
        _condition("custom_field", MONTANT, "is_missing"),
        operator="or",
    )
    refusal = client.post(
        "/api/v1/documents/query", json={"filters": step11, "page_size": 25}
    )
    assert refusal.status_code == 422
    assert refusal.json()["error"]["code"] == "FILTER_NOT_COMPILABLE"
    issue = refusal.json()["error"]["details"]["issues"][0]
    assert issue["code"] == "MIXED_OR_UNSUPPORTED"
    assert "OR" in issue["message"]

    # Counting it is refused the same way - never a number that means
    # something other than what the user asked.
    assert (
        client.post("/api/v1/filters/count", json={"filters": step11}).status_code == 422
    )

    # /validate still answers 200 and says which of the two verdicts failed:
    # the filter is well-formed, Paperless just cannot express it.
    report = client.post("/api/v1/filters/validate", json={"filters": step11}).json()
    assert report["valid"] is True
    assert report["compilable"] is False

    # -- 13. No fetch-all occurred -------------------------------------------
    assert documents.call_count == calls_before_refusal, (
        "the refused filter must not have reached Paperless at all"
    )
    for call in documents.calls:
        size = int(dict(call.request.url.params)["page_size"])
        assert size <= 250, "no request may ask for more than one page of documents"

    # -- 14. No document was modified ----------------------------------------
    assert not writes.called
