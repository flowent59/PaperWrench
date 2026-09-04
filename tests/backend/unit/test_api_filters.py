"""Contract tests for the Filter Engine's API surface.

Three endpoints, three different contracts, and the differences matter:

* ``/validate`` **reports** - a builder UI calls it while the user types and
  needs both verdicts, not an exception.
* ``/count`` **raises** - a count is used to decide something, so an
  uncompilable filter must be an error rather than a number that quietly
  means something else.
* ``/capabilities`` describes *this* instance, with custom fields coming from
  the live Metadata Registry rather than anything hardcoded.
"""

from __future__ import annotations

import json
from typing import Any

import respx
from fastapi.testclient import TestClient
from httpx import Request
from httpx import Response

BASE = "http://paperless.test"
V10_HEADERS = {"X-Api-Version": "10", "X-Version": "3.1.2"}

MONTANT = 2
PERIODE = 1
CATEGORIE = 7
OPTION_URGENT = "gsbRSetmXYcC2nKx"


def _page(results: list[dict[str, Any]], count: int | None = None) -> dict[str, Any]:
    return {
        "count": count if count is not None else len(results),
        "next": None,
        "previous": None,
        "results": results,
    }


CUSTOM_FIELDS: list[dict[str, Any]] = [
    {"id": PERIODE, "name": "Période concernée", "data_type": "string", "extra_data": None},
    {
        "id": MONTANT,
        "name": "Montant",
        "data_type": "monetary",
        "extra_data": {"default_currency": "EUR"},
    },
    {
        "id": CATEGORIE,
        "name": "Catégorie",
        "data_type": "select",
        "extra_data": {
            "select_options": [
                {"id": OPTION_URGENT, "label": "Urgent"},
                {"id": "kQ4tXbYcZ2mNp7Rd", "label": "Normal"},
            ]
        },
    },
]


def _mock_metadata() -> None:
    for endpoint in ("tags", "correspondents", "document_types", "storage_paths"):
        respx.get(f"{BASE}/api/{endpoint}/").mock(
            return_value=Response(200, json=_page([]), headers=V10_HEADERS)
        )
    respx.get(f"{BASE}/api/custom_fields/").mock(
        return_value=Response(200, json=_page(CUSTOM_FIELDS), headers=V10_HEADERS)
    )


def _condition(source: str, key: Any, operator: str, value: Any = None) -> dict[str, Any]:
    field = (
        {"source": "core", "name": key}
        if source == "core"
        else {"source": "custom_field", "field_id": key}
    )
    return {"kind": "condition", "field": field, "operator": operator, "value": value}


def _filters(*children: dict[str, Any], operator: str = "and") -> dict[str, Any]:
    return {"root": {"kind": "group", "operator": operator, "children": list(children)}}


# ============================================================== capabilities
@respx.mock
def test_capabilities_lists_core_and_custom_fields(client: TestClient) -> None:
    _mock_metadata()

    response = client.get("/api/v1/filters/capabilities")

    assert response.status_code == 200
    payload = response.json()
    keys = {field["key"] for field in payload["fields"]}
    assert "core:title" in keys
    assert "core:tags" in keys
    assert f"custom_field:{MONTANT}" in keys


@respx.mock
def test_capability_custom_fields_come_from_live_metadata_not_hardcoded_names(
    client: TestClient,
) -> None:
    """The Filter Builder's field list must follow Paperless, not a constant."""
    _mock_metadata()

    payload = client.get("/api/v1/filters/capabilities").json()
    custom = {
        field["label"]: field for field in payload["fields"] if field["source"] == "custom_field"
    }

    assert set(custom) == {"Période concernée", "Montant", "Catégorie"}
    assert custom["Montant"]["custom_field_id"] == MONTANT
    assert custom["Montant"]["field_type"] == "monetary"


@respx.mock
def test_monetary_offers_comparisons_and_the_missing_family_but_not_contains(
    client: TestClient,
) -> None:
    """The M4 brief's worked example of a type-driven operator list."""
    _mock_metadata()

    payload = client.get("/api/v1/filters/capabilities").json()
    montant = next(f for f in payload["fields"] if f["custom_field_id"] == MONTANT)
    operators = {op["operator"] for op in montant["operators"]}

    assert {"equals", "greater_than", "less_than", "is_missing"} <= operators
    assert "contains" not in operators
    assert "is_empty" not in operators


@respx.mock
def test_a_boolean_custom_field_never_offers_contains(client: TestClient) -> None:
    _mock_metadata()
    respx.get(f"{BASE}/api/custom_fields/").mock(
        return_value=Response(
            200,
            json=_page(
                [{"id": 3, "name": "Validé", "data_type": "boolean", "extra_data": None}]
            ),
            headers=V10_HEADERS,
        )
    )

    payload = client.get("/api/v1/filters/capabilities").json()
    valide = next(f for f in payload["fields"] if f["custom_field_id"] == 3)

    assert {op["operator"] for op in valide["operators"]} == {
        "equals",
        "is_missing",
        "is_present",
        "is_null",
        "has_value",
    }


@respx.mock
def test_select_options_expose_the_id_as_identity_and_the_label_as_display(
    client: TestClient,
) -> None:
    _mock_metadata()

    payload = client.get("/api/v1/filters/capabilities").json()
    categorie = next(f for f in payload["fields"] if f["custom_field_id"] == CATEGORIE)

    assert {option["id"] for option in categorie["select_options"]} == {
        OPTION_URGENT,
        "kQ4tXbYcZ2mNp7Rd",
    }
    assert {option["label"] for option in categorie["select_options"]} == {"Urgent", "Normal"}


@respx.mock
def test_grouping_capabilities_state_what_the_compiler_refuses(client: TestClient) -> None:
    """The UI reads these to avoid offering shapes that will be refused."""
    _mock_metadata()

    grouping = client.get("/api/v1/filters/capabilities").json()["grouping"]

    assert grouping["and_supported"] is True
    assert grouping["or_custom_fields_supported"] is True
    assert grouping["or_core_fields_supported"] is False
    assert grouping["or_mixed_supported"] is False
    assert grouping["not_supported"] is False


@respx.mock
def test_capabilities_describe_the_three_search_modes_distinctly(client: TestClient) -> None:
    _mock_metadata()

    modes = client.get("/api/v1/filters/capabilities").json()["search_modes"]

    assert {mode["mode"] for mode in modes} == {"title", "content", "advanced"}
    title = next(mode for mode in modes if mode["mode"] == "title")
    assert "title" in title["description"].lower()


@respx.mock
def test_operator_semantics_spell_out_the_empty_missing_distinction(
    client: TestClient,
) -> None:
    _mock_metadata()

    semantics = client.get("/api/v1/filters/capabilities").json()["operator_semantics"]

    assert "ABSENT" in semantics["is_missing"]
    assert "Never matches" in semantics["is_null"]
    assert "does NOT match" in semantics["is_empty"]


@respx.mock
def test_a_case_insensitivity_note_is_attached_to_text_equality(client: TestClient) -> None:
    """A subtly different filter is worse than a visibly limited one."""
    _mock_metadata()

    payload = client.get("/api/v1/filters/capabilities").json()
    title = next(f for f in payload["fields"] if f["key"] == "core:title")
    equals = next(op for op in title["operators"] if op["operator"] == "equals")

    assert equals["note"] is not None
    assert "case-insensitive" in equals["note"].lower()


@respx.mock
def test_valueless_operators_declare_that_they_take_no_value(client: TestClient) -> None:
    _mock_metadata()

    payload = client.get("/api/v1/filters/capabilities").json()
    montant = next(f for f in payload["fields"] if f["custom_field_id"] == MONTANT)
    is_missing = next(op for op in montant["operators"] if op["operator"] == "is_missing")
    equals = next(op for op in montant["operators"] if op["operator"] == "equals")

    assert is_missing["value_shape"] == "none"
    assert equals["value_shape"] == "decimal"


@respx.mock
def test_list_operators_declare_themselves_multi_valued(client: TestClient) -> None:
    _mock_metadata()

    payload = client.get("/api/v1/filters/capabilities").json()
    tags = next(f for f in payload["fields"] if f["key"] == "core:tags")
    has_all = next(op for op in tags["operators"] if op["operator"] == "has_all_of")

    assert has_all["multi"] is True
    assert has_all["value_shape"] == "reference_id"
    assert tags["reference_kind"] == "tag"


# ================================================================= validate
@respx.mock
def test_validate_reports_a_good_filter_as_valid_and_compilable(client: TestClient) -> None:
    _mock_metadata()

    response = client.post(
        "/api/v1/filters/validate",
        json={"filters": _filters(_condition("custom_field", MONTANT, "is_missing"))},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["valid"] is True
    assert payload["compilable"] is True
    assert payload["issues"] == []
    assert payload["compiled"]["params"]["custom_field_query"] == '[2,"exists",false]'


@respx.mock
def test_validate_distinguishes_invalid_from_valid_but_not_compilable(
    client: TestClient,
) -> None:
    """The two states the M4 brief asked to be kept apart.

    They call for different things from the user: one is a mistake to fix,
    the other is a limit of the server to work around.
    """
    _mock_metadata()

    invalid = client.post(
        "/api/v1/filters/validate",
        json={"filters": _filters(_condition("custom_field", 9999, "equals", "x"))},
    ).json()
    uncompilable = client.post(
        "/api/v1/filters/validate",
        json={
            "filters": _filters(
                _condition("core", "document_type", "equals", 3),
                _condition("custom_field", MONTANT, "is_missing"),
                operator="or",
            )
        },
    ).json()

    assert (invalid["valid"], invalid["compilable"]) == (False, False)
    assert invalid["issues"][0]["stage"] == "validation"
    assert invalid["issues"][0]["code"] == "UNKNOWN_FIELD"

    assert (uncompilable["valid"], uncompilable["compilable"]) == (True, False)
    assert uncompilable["issues"][0]["stage"] == "compilation"
    assert uncompilable["issues"][0]["code"] == "MIXED_OR_UNSUPPORTED"


@respx.mock
def test_validate_returns_200_even_for_a_broken_filter(client: TestClient) -> None:
    """It is a report, not a submission."""
    _mock_metadata()

    response = client.post(
        "/api/v1/filters/validate",
        json={"filters": _filters(_condition("core", "title", "contains", ""))},
    )

    assert response.status_code == 200
    assert response.json()["valid"] is False


@respx.mock
def test_validate_issues_carry_a_path_the_builder_can_highlight(client: TestClient) -> None:
    _mock_metadata()

    payload = client.post(
        "/api/v1/filters/validate",
        json={
            "filters": _filters(
                _condition("custom_field", MONTANT, "is_missing"),
                _condition("custom_field", MONTANT, "equals", 12.5),
            )
        },
    ).json()

    assert payload["issues"][0]["path"] == "root.children[1].value"
    assert payload["issues"][0]["field"] == f"custom_field:{MONTANT}"


@respx.mock
def test_validate_exposes_the_compiled_query_for_explanation(client: TestClient) -> None:
    _mock_metadata()

    payload = client.post(
        "/api/v1/filters/validate",
        json={
            "filters": _filters(
                _condition("core", "document_type", "equals", 3),
                _condition("custom_field", MONTANT, "greater_than", "EUR0.00"),
            )
        },
    ).json()

    assert payload["compiled"]["params"]["document_type__id"] == "3"
    assert payload["compiled"]["custom_field_expression"] == [MONTANT, "gt", "0.00"]


@respx.mock
def test_validate_accepts_an_empty_filterset(client: TestClient) -> None:
    _mock_metadata()

    payload = client.post("/api/v1/filters/validate", json={"filters": {}}).json()

    assert payload["valid"] is True
    assert payload["compiled"]["params"] == {}


# ==================================================================== count
@respx.mock
def test_count_returns_the_paperless_count_without_fetching_the_documents(
    client: TestClient,
) -> None:
    """Counting is not fetching: one request, one document of payload, any size."""
    _mock_metadata()
    route = respx.get(f"{BASE}/api/documents/").mock(
        return_value=Response(
            200,
            json={"count": 1438, "next": None, "previous": None, "results": []},
            headers=V10_HEADERS,
        )
    )

    response = client.post(
        "/api/v1/filters/count",
        json={"filters": _filters(_condition("custom_field", MONTANT, "is_missing"))},
    )

    assert response.status_code == 200
    assert response.json()["count"] == 1438
    assert route.call_count == 1
    request: Request = route.calls.last.request
    assert request.url.params["page_size"] == "1"
    assert request.url.params["page"] == "1"


@respx.mock
def test_count_sends_exactly_the_compiled_parameters(client: TestClient) -> None:
    _mock_metadata()
    route = respx.get(f"{BASE}/api/documents/").mock(
        return_value=Response(
            200,
            json={"count": 3, "next": None, "previous": None, "results": []},
            headers=V10_HEADERS,
        )
    )

    response = client.post(
        "/api/v1/filters/count",
        json={
            "filters": _filters(
                _condition("core", "document_type", "equals", 3),
                _condition("custom_field", MONTANT, "is_missing"),
            )
        },
    )

    request: Request = route.calls.last.request
    assert request.url.params["document_type__id"] == "3"
    assert json.loads(request.url.params["custom_field_query"]) == [MONTANT, "exists", False]
    assert response.json()["compiled"]["params"]["document_type__id"] == "3"


@respx.mock
def test_count_includes_the_search_so_it_matches_what_the_explorer_shows(
    client: TestClient,
) -> None:
    _mock_metadata()
    route = respx.get(f"{BASE}/api/documents/").mock(
        return_value=Response(
            200,
            json={"count": 7, "next": None, "previous": None, "results": []},
            headers=V10_HEADERS,
        )
    )

    client.post(
        "/api/v1/filters/count",
        json={
            "filters": _filters(_condition("custom_field", MONTANT, "is_missing")),
            "search": {"mode": "title", "text": "vacations"},
        },
    )

    request: Request = route.calls.last.request
    assert request.url.params["title_search"] == "vacations"


@respx.mock
def test_count_raises_for_an_uncompilable_filter_rather_than_returning_a_number(
    client: TestClient,
) -> None:
    """A count decides something. A misleading one is worse than an error."""
    _mock_metadata()
    route = respx.get(f"{BASE}/api/documents/").mock(
        return_value=Response(
            200,
            json={"count": 50_000, "next": None, "previous": None, "results": []},
            headers=V10_HEADERS,
        )
    )

    response = client.post(
        "/api/v1/filters/count",
        json={
            "filters": _filters(
                _condition("core", "title", "contains", "a"),
                _condition("custom_field", MONTANT, "is_missing"),
                operator="or",
            )
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "FILTER_NOT_COMPILABLE"
    assert route.call_count == 0


@respx.mock
def test_count_of_an_empty_filterset_is_the_whole_library(client: TestClient) -> None:
    """Not an error - but the compiled params make the absence of a filter visible."""
    _mock_metadata()
    respx.get(f"{BASE}/api/documents/").mock(
        return_value=Response(
            200,
            json={"count": 17, "next": None, "previous": None, "results": []},
            headers=V10_HEADERS,
        )
    )

    payload = client.post("/api/v1/filters/count", json={}).json()

    assert payload["count"] == 17
    assert payload["compiled"]["params"] == {}


@respx.mock
def test_count_does_not_accept_an_ordering(client: TestClient) -> None:
    """Ordering cannot change a count; accepting it would suggest otherwise."""
    _mock_metadata()

    response = client.post("/api/v1/filters/count", json={"ordering": "-created"})

    assert response.status_code == 422


@respx.mock
def test_the_filters_router_is_registered_in_the_openapi_schema(client: TestClient) -> None:
    paths = client.get("/api/openapi.json").json()["paths"]

    assert "/api/v1/filters/validate" in paths
    assert "/api/v1/filters/count" in paths
    assert "/api/v1/filters/capabilities" in paths


@respx.mock
def test_no_filter_endpoint_ever_writes_to_paperless(client: TestClient) -> None:
    _mock_metadata()
    respx.get(f"{BASE}/api/documents/").mock(
        return_value=Response(
            200,
            json={"count": 1, "next": None, "previous": None, "results": []},
            headers=V10_HEADERS,
        )
    )
    write_route = respx.route(
        method__in=["PATCH", "POST", "PUT", "DELETE"], host="paperless.test"
    ).mock(return_value=Response(500, json={"detail": "should never be called"}))

    client.get("/api/v1/filters/capabilities")
    client.post(
        "/api/v1/filters/validate",
        json={"filters": _filters(_condition("custom_field", MONTANT, "is_missing"))},
    )
    client.post(
        "/api/v1/filters/count",
        json={"filters": _filters(_condition("custom_field", MONTANT, "is_missing"))},
    )

    assert not write_route.called
