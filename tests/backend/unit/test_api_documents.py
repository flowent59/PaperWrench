"""Contract tests for the Explorer's documents API (M3, rebuilt in M4).

These are unit tests against the FastAPI app with ``respx`` mocking the
Paperless side. They prove PaperWrench's own endpoint shape, its ordering
allowlist, its page-size validation and its normalization - never that
Paperless's own shapes are real (see tests/backend/live for that).

M4 replaced M3's ``GET /api/v1/documents`` (which carried three ad-hoc
filter parameters) with ``POST /api/v1/documents/query``, whose body is a
dataset page request. Every M3 guarantee asserted here still holds; only the
way the request is expressed changed. The filtering assertions in particular
now go through the Filter Engine, which is the point: there is one filtering
path, not two.
"""

from __future__ import annotations

import respx
from fastapi.testclient import TestClient
from httpx import Request
from httpx import Response

from paperwrench.api.v1.documents import CORE_ORDERING_FIELDS
from paperwrench.api.v1.documents import SORTABLE_CUSTOM_FIELD_DATA_TYPES
from paperwrench.api.v1.documents import resolve_ordering
from paperwrench.errors import InvalidOrderingError

BASE = "http://paperless.test"
V10_HEADERS = {"X-Api-Version": "10", "X-Version": "3.1.2"}


def _page(results: list[dict[str, object]], count: int | None = None) -> dict[str, object]:
    return {
        "count": count if count is not None else len(results),
        "next": None,
        "previous": None,
        "results": results,
    }


def _mock_reference_endpoints(
    *,
    tags: list[dict[str, object]] | None = None,
    correspondents: list[dict[str, object]] | None = None,
    document_types: list[dict[str, object]] | None = None,
    storage_paths: list[dict[str, object]] | None = None,
    custom_fields: list[dict[str, object]] | None = None,
) -> None:
    respx.get(f"{BASE}/api/tags/").mock(
        return_value=Response(200, json=_page(tags or []), headers=V10_HEADERS)
    )
    respx.get(f"{BASE}/api/correspondents/").mock(
        return_value=Response(200, json=_page(correspondents or []), headers=V10_HEADERS)
    )
    respx.get(f"{BASE}/api/document_types/").mock(
        return_value=Response(200, json=_page(document_types or []), headers=V10_HEADERS)
    )
    respx.get(f"{BASE}/api/storage_paths/").mock(
        return_value=Response(200, json=_page(storage_paths or []), headers=V10_HEADERS)
    )
    respx.get(f"{BASE}/api/custom_fields/").mock(
        return_value=Response(200, json=_page(custom_fields or []), headers=V10_HEADERS)
    )


def _core(operator: str, name: str, value: object = None) -> dict[str, object]:
    """One core-field condition, in wire form."""
    return {
        "kind": "condition",
        "field": {"source": "core", "name": name},
        "operator": operator,
        "value": value,
    }


def _custom(operator: str, field_id: int, value: object = None) -> dict[str, object]:
    """One custom-field condition, in wire form."""
    return {
        "kind": "condition",
        "field": {"source": "custom_field", "field_id": field_id},
        "operator": operator,
        "value": value,
    }


def _filters(*children: dict[str, object], operator: str = "and") -> dict[str, object]:
    return {"root": {"kind": "group", "operator": operator, "children": list(children)}}


def _document(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": 100,
        "title": "Test Doc",
        "correspondent": 7,
        "document_type": 3,
        "storage_path": None,
        "tags": [1],
        "created": "2024-01-01T00:00:00Z",
        "modified": "2024-01-02T00:00:00Z",
        "added": "2024-01-01T00:00:00Z",
        "archive_serial_number": None,
        "original_file_name": "test.pdf",
        "owner": 1,
        "custom_fields": [],
        "user_can_change": True,
        "deleted_at": None,
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------- basic shape
@respx.mock
def test_list_documents_returns_normalized_page_envelope(client: TestClient) -> None:
    _mock_reference_endpoints(
        tags=[{"id": 1, "name": "Urgent"}],
        correspondents=[{"id": 7, "name": "CH Valenciennes"}],
        document_types=[{"id": 3, "name": "Facture"}],
    )
    respx.get(f"{BASE}/api/documents/").mock(
        return_value=Response(200, json=_page([_document()], count=1), headers=V10_HEADERS)
    )

    response = client.post("/api/v1/documents/query", json={"page": 1, "page_size": 25})

    assert response.status_code == 200
    payload = response.json()
    assert payload["page"] == 1
    assert payload["page_size"] == 25
    assert payload["total"] == 1
    assert payload["page_count"] == 1
    assert len(payload["items"]) == 1

    item = payload["items"][0]
    assert item["id"] == 100
    assert item["title"] == "Test Doc"
    assert item["correspondent"] == {"id": 7, "name": "CH Valenciennes"}
    assert item["document_type"] == {"id": 3, "name": "Facture"}
    assert item["tags"] == [{"id": 1, "name": "Urgent"}]
    assert item["user_can_change"] is True


@respx.mock
def test_list_documents_never_leaks_the_paperless_token(client: TestClient) -> None:
    _mock_reference_endpoints()
    respx.get(f"{BASE}/api/documents/").mock(
        return_value=Response(500, json={"detail": "boom"}, headers=V10_HEADERS)
    )

    response = client.post("/api/v1/documents/query", json={})

    assert "test-token-abcdef123456" not in response.text


def test_documents_router_is_registered_in_the_openapi_schema(client: TestClient) -> None:
    response = client.get("/api/openapi.json")
    assert response.status_code == 200
    assert "/api/v1/documents/query" in response.json()["paths"]


# ------------------------------------------------------------- pagination
@respx.mock
def test_page_count_is_computed_from_total_and_page_size(client: TestClient) -> None:
    _mock_reference_endpoints()
    respx.get(f"{BASE}/api/documents/").mock(
        return_value=Response(200, json=_page([], count=101), headers=V10_HEADERS)
    )

    response = client.post("/api/v1/documents/query", json={"page_size": 25})

    assert response.status_code == 200
    assert response.json()["page_count"] == 5  # ceil(101 / 25)


@respx.mock
def test_page_size_default_is_one_hundred(client: TestClient) -> None:
    _mock_reference_endpoints()
    route = respx.get(f"{BASE}/api/documents/").mock(
        return_value=Response(200, json=_page([], count=0), headers=V10_HEADERS)
    )

    response = client.post("/api/v1/documents/query", json={})

    assert response.status_code == 200
    assert response.json()["page_size"] == 100
    request: Request = route.calls.last.request
    assert request.url.params["page_size"] == "100"


@respx.mock
def test_invalid_page_size_is_rejected_with_422_and_never_forwarded(client: TestClient) -> None:
    _mock_reference_endpoints()
    route = respx.get(f"{BASE}/api/documents/").mock(
        return_value=Response(200, json=_page([], count=0), headers=V10_HEADERS)
    )

    response = client.post("/api/v1/documents/query", json={"page_size": 13})

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["error"]["params"]["page_size"] == 13
    assert body["error"]["params"]["allowed"] == [25, 50, 100, 250]
    assert body["error"]["details"]["page_size"] == 13
    assert body["error"]["details"]["allowed"] == [25, 50, 100, 250]
    assert not route.called  # never reached Paperless


@respx.mock
def test_there_is_no_all_page_size(client: TestClient) -> None:
    _mock_reference_endpoints()
    respx.get(f"{BASE}/api/documents/").mock(
        return_value=Response(200, json=_page([], count=0), headers=V10_HEADERS)
    )

    for size in (0, -1, 1000, 251):
        response = client.post("/api/v1/documents/query", json={"page_size": size})
        assert response.status_code == 422, f"page_size={size} should be rejected"


# ------------------------------------------------------------------ search
@respx.mock
def test_search_maps_to_title_search(client: TestClient) -> None:
    _mock_reference_endpoints()
    route = respx.get(f"{BASE}/api/documents/").mock(
        return_value=Response(200, json=_page([], count=0), headers=V10_HEADERS)
    )

    response = client.post(
        "/api/v1/documents/query",
        json={"search": {"mode": "title", "text": "vacations"}},
    )

    assert response.status_code == 200
    request: Request = route.calls.last.request
    assert request.url.params["title_search"] == "vacations"
    assert "search" not in request.url.params


@respx.mock
def test_query_maps_to_paperless_query_as_is(client: TestClient) -> None:
    _mock_reference_endpoints()
    route = respx.get(f"{BASE}/api/documents/").mock(
        return_value=Response(200, json=_page([], count=0), headers=V10_HEADERS)
    )

    response = client.post(
        "/api/v1/documents/query",
        json={"search": {"mode": "advanced", "text": 'title:"foo bar"'}},
    )

    assert response.status_code == 200
    request: Request = route.calls.last.request
    assert request.url.params["query"] == 'title:"foo bar"'


@respx.mock
def test_only_one_search_mode_can_be_expressed_at_a_time(client: TestClient) -> None:
    """M3 could send `search` and `query` together and be rejected downstream.

    M4 makes that unrepresentable rather than merely invalid: a SearchSpec
    carries exactly one mode. Paperless returns 400 when more than one of
    text/title_search/query/more_like_id is present (VERIFIED_SOURCE), and
    the shape of the request body now makes that impossible to build.
    """
    _mock_reference_endpoints()
    route = respx.get(f"{BASE}/api/documents/").mock(
        return_value=Response(200, json=_page([], count=0), headers=V10_HEADERS)
    )

    response = client.post(
        "/api/v1/documents/query",
        json={"search": {"mode": "nonsense", "text": "a"}},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert not route.called


@respx.mock
def test_an_empty_search_is_refused_rather_than_silently_dropped(client: TestClient) -> None:
    """An all-whitespace search must not become "no search" behind the user.

    Sending `title_search=` puts Paperless into search mode with an empty
    Tantivy query - a different code path from not searching at all - and
    dropping the parameter silently would make an empty search box mean
    "every document" without saying so.
    """
    _mock_reference_endpoints()
    route = respx.get(f"{BASE}/api/documents/").mock(
        return_value=Response(200, json=_page([], count=0), headers=V10_HEADERS)
    )

    response = client.post(
        "/api/v1/documents/query", json={"search": {"mode": "title", "text": "   "}}
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert not route.called


@respx.mock
def test_content_search_mode_maps_to_the_text_parameter(client: TestClient) -> None:
    """The three search modes are three different Paperless parameters.

    M3's single `search` parameter always meant `title_search` and said so
    nowhere; naming the mode is the whole point of SearchSpec (ADR-0010).
    """
    _mock_reference_endpoints()
    route = respx.get(f"{BASE}/api/documents/").mock(
        return_value=Response(200, json=_page([], count=0), headers=V10_HEADERS)
    )

    response = client.post(
        "/api/v1/documents/query",
        json={"search": {"mode": "content", "text": "vacations"}},
    )

    assert response.status_code == 200
    request: Request = route.calls.last.request
    assert request.url.params["text"] == "vacations"
    assert "title_search" not in request.url.params
    assert "query" not in request.url.params


# ---------------------------------------------------------------- ordering
@respx.mock
def test_ordering_core_field_is_translated_and_forwarded(client: TestClient) -> None:
    _mock_reference_endpoints()
    route = respx.get(f"{BASE}/api/documents/").mock(
        return_value=Response(200, json=_page([], count=0), headers=V10_HEADERS)
    )

    response = client.post("/api/v1/documents/query", json={"ordering": "-created"})

    assert response.status_code == 200
    request: Request = route.calls.last.request
    assert request.url.params["ordering"] == "-created"


@respx.mock
def test_ordering_correspondent_translates_to_correspondent_name(client: TestClient) -> None:
    _mock_reference_endpoints()
    route = respx.get(f"{BASE}/api/documents/").mock(
        return_value=Response(200, json=_page([], count=0), headers=V10_HEADERS)
    )

    response = client.post("/api/v1/documents/query", json={"ordering": "correspondent"})

    assert response.status_code == 200
    request: Request = route.calls.last.request
    assert request.url.params["ordering"] == "correspondent__name"


@respx.mock
def test_invalid_ordering_is_rejected_and_never_forwarded_to_paperless(
    client: TestClient,
) -> None:
    _mock_reference_endpoints()
    route = respx.get(f"{BASE}/api/documents/").mock(
        return_value=Response(200, json=_page([], count=0), headers=V10_HEADERS)
    )

    response = client.post("/api/v1/documents/query", json={"ordering": "not_a_real_field"})

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["error"]["params"]["ordering"] == "not_a_real_field"
    assert body["error"]["details"]["ordering"] == "not_a_real_field"
    assert not route.called  # the whole point: never forwarded


@respx.mock
def test_ordering_by_a_bare_id_style_paperless_field_is_still_rejected(
    client: TestClient,
) -> None:
    """Paperless accepts ``ordering=owner``; PaperWrench does not expose it (yet)."""
    _mock_reference_endpoints()
    route = respx.get(f"{BASE}/api/documents/").mock(
        return_value=Response(200, json=_page([], count=0), headers=V10_HEADERS)
    )

    response = client.post("/api/v1/documents/query", json={"ordering": "owner"})

    assert response.status_code == 422
    assert not route.called


@respx.mock
def test_ordering_by_known_monetary_custom_field_is_accepted(client: TestClient) -> None:
    _mock_reference_endpoints(
        custom_fields=[{"id": 42, "name": "Montant", "data_type": "monetary", "extra_data": None}]
    )
    route = respx.get(f"{BASE}/api/documents/").mock(
        return_value=Response(200, json=_page([], count=0), headers=V10_HEADERS)
    )

    response = client.post("/api/v1/documents/query", json={"ordering": "custom_field_42"})

    assert response.status_code == 200
    request: Request = route.calls.last.request
    assert request.url.params["ordering"] == "custom_field_42"


@respx.mock
def test_ordering_by_known_date_custom_field_is_accepted_descending(client: TestClient) -> None:
    _mock_reference_endpoints(
        custom_fields=[
            {"id": 11, "name": "Date de reglement", "data_type": "date", "extra_data": None}
        ]
    )
    route = respx.get(f"{BASE}/api/documents/").mock(
        return_value=Response(200, json=_page([], count=0), headers=V10_HEADERS)
    )

    response = client.post("/api/v1/documents/query", json={"ordering": "-custom_field_11"})

    assert response.status_code == 200
    request: Request = route.calls.last.request
    assert request.url.params["ordering"] == "-custom_field_11"


@respx.mock
def test_ordering_by_unknown_custom_field_id_is_rejected(client: TestClient) -> None:
    _mock_reference_endpoints(
        custom_fields=[{"id": 42, "name": "Montant", "data_type": "monetary", "extra_data": None}]
    )
    route = respx.get(f"{BASE}/api/documents/").mock(
        return_value=Response(200, json=_page([], count=0), headers=V10_HEADERS)
    )

    response = client.post("/api/v1/documents/query", json={"ordering": "custom_field_9999"})

    assert response.status_code == 422
    assert not route.called


@respx.mock
def test_ordering_by_a_select_type_custom_field_is_rejected_in_m3(client: TestClient) -> None:
    """M3 only exposes ordering for Text/Long text, Monetary and Date (see brief).

    Even though Paperless itself (VERIFIED_SOURCE) implements
    ``custom_field_<id>`` ordering for SELECT too, PaperWrench does not
    expose it yet - unreliable/unverified behaviour must not be surfaced.
    """
    _mock_reference_endpoints(
        custom_fields=[
            {
                "id": 55,
                "name": "Statut",
                "data_type": "select",
                "extra_data": {"select_options": []},
            }
        ]
    )
    route = respx.get(f"{BASE}/api/documents/").mock(
        return_value=Response(200, json=_page([], count=0), headers=V10_HEADERS)
    )

    response = client.post("/api/v1/documents/query", json={"ordering": "custom_field_55"})

    assert response.status_code == 422
    assert not route.called


@respx.mock
def test_ordering_by_a_boolean_type_custom_field_is_rejected_in_m3(client: TestClient) -> None:
    _mock_reference_endpoints(
        custom_fields=[{"id": 66, "name": "Valide", "data_type": "boolean", "extra_data": None}]
    )
    route = respx.get(f"{BASE}/api/documents/").mock(
        return_value=Response(200, json=_page([], count=0), headers=V10_HEADERS)
    )

    response = client.post("/api/v1/documents/query", json={"ordering": "custom_field_66"})

    assert response.status_code == 422
    assert not route.called


def test_resolve_ordering_helper_returns_none_for_no_ordering() -> None:
    assert resolve_ordering(None, sortable_custom_field_ids=frozenset()) is None


def test_resolve_ordering_helper_raises_for_unknown_value() -> None:
    try:
        resolve_ordering("bogus", sortable_custom_field_ids=frozenset())
    except InvalidOrderingError as exc:
        assert exc.status_code == 422
    else:  # pragma: no cover - defensive
        raise AssertionError("expected InvalidOrderingError")


def test_core_ordering_fields_are_the_documented_subset() -> None:
    assert set(CORE_ORDERING_FIELDS) == {
        "title",
        "created",
        "modified",
        "added",
        "archive_serial_number",
        "correspondent",
        "document_type",
    }


def test_sortable_custom_field_data_types_are_the_documented_subset() -> None:
    assert frozenset({"string", "longtext", "monetary", "date"}) == SORTABLE_CUSTOM_FIELD_DATA_TYPES


# ------------------------------------------------------------------ filters
@respx.mock
def test_document_type_filter_maps_to_paperless_param(client: TestClient) -> None:
    _mock_reference_endpoints()
    route = respx.get(f"{BASE}/api/documents/").mock(
        return_value=Response(200, json=_page([], count=0), headers=V10_HEADERS)
    )

    response = client.post(
        "/api/v1/documents/query",
        json={"filters": _filters(_core("equals", "document_type", 3))},
    )

    assert response.status_code == 200
    request: Request = route.calls.last.request
    assert request.url.params["document_type__id"] == "3"


@respx.mock
def test_correspondent_filter_maps_to_paperless_param(client: TestClient) -> None:
    _mock_reference_endpoints()
    route = respx.get(f"{BASE}/api/documents/").mock(
        return_value=Response(200, json=_page([], count=0), headers=V10_HEADERS)
    )

    response = client.post(
        "/api/v1/documents/query",
        json={"filters": _filters(_core("equals", "correspondent", 7))},
    )

    assert response.status_code == 200
    request: Request = route.calls.last.request
    assert request.url.params["correspondent__id"] == "7"


@respx.mock
def test_tag_filter_maps_to_paperless_param(client: TestClient) -> None:
    _mock_reference_endpoints()
    route = respx.get(f"{BASE}/api/documents/").mock(
        return_value=Response(200, json=_page([], count=0), headers=V10_HEADERS)
    )

    response = client.post(
        "/api/v1/documents/query",
        json={"filters": _filters(_core("has_all_of", "tags", [1]))},
    )

    assert response.status_code == 200
    request: Request = route.calls.last.request
    assert request.url.params["tags__id__all"] == "1"


@respx.mock
def test_search_pagination_and_ordering_compose_in_a_single_request(client: TestClient) -> None:
    _mock_reference_endpoints()
    route = respx.get(f"{BASE}/api/documents/").mock(
        return_value=Response(200, json=_page([], count=0), headers=V10_HEADERS)
    )

    response = client.post(
        "/api/v1/documents/query",
        json={
            "search": {"mode": "title", "text": "vacations"},
            "ordering": "-created",
            "page": 2,
            "page_size": 50,
            "filters": _filters(_core("equals", "document_type", 3)),
        },
    )

    assert response.status_code == 200
    request: Request = route.calls.last.request
    assert request.url.params["title_search"] == "vacations"
    assert request.url.params["ordering"] == "-created"
    assert request.url.params["page"] == "2"
    assert request.url.params["page_size"] == "50"
    assert request.url.params["document_type__id"] == "3"


# ---------------------------------------------------------- normalization
@respx.mock
def test_unresolvable_correspondent_renders_as_unresolved_reference_not_null(
    client: TestClient,
) -> None:
    """A correspondent id the registry does not know about must not be dropped."""
    _mock_reference_endpoints()  # no correspondents registered at all
    respx.get(f"{BASE}/api/documents/").mock(
        return_value=Response(
            200,
            json=_page([_document(correspondent=999, document_type=None, tags=[])], count=1),
            headers=V10_HEADERS,
        )
    )

    response = client.post("/api/v1/documents/query", json={})

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["correspondent"] == {"id": 999, "name": None}


@respx.mock
def test_unresolvable_tag_renders_as_unresolved_reference(client: TestClient) -> None:
    _mock_reference_endpoints()  # no tags registered
    respx.get(f"{BASE}/api/documents/").mock(
        return_value=Response(
            200,
            json=_page(
                [_document(correspondent=None, document_type=None, tags=[123])], count=1
            ),
            headers=V10_HEADERS,
        )
    )

    response = client.post("/api/v1/documents/query", json={})

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["tags"] == [{"id": 123, "name": None}]


@respx.mock
def test_null_correspondent_and_document_type_render_as_null_not_unresolved(
    client: TestClient,
) -> None:
    _mock_reference_endpoints()
    respx.get(f"{BASE}/api/documents/").mock(
        return_value=Response(
            200,
            json=_page(
                [_document(correspondent=None, document_type=None, tags=[])], count=1
            ),
            headers=V10_HEADERS,
        )
    )

    response = client.post("/api/v1/documents/query", json={})

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["correspondent"] is None
    assert item["document_type"] is None


@respx.mock
def test_custom_fields_include_absent_fields_for_every_known_field(client: TestClient) -> None:
    """Every registry-known custom field appears, even if this doc lacks it (ABSENT)."""
    _mock_reference_endpoints(
        custom_fields=[
            {"id": 42, "name": "Montant", "data_type": "monetary", "extra_data": None},
            {"id": 43, "name": "Commentaire", "data_type": "string", "extra_data": None},
        ]
    )
    respx.get(f"{BASE}/api/documents/").mock(
        return_value=Response(
            200,
            json=_page(
                [
                    _document(
                        correspondent=None,
                        document_type=None,
                        tags=[],
                        custom_fields=[{"field": 42, "value": "EUR450.00"}],
                    )
                ],
                count=1,
            ),
            headers=V10_HEADERS,
        )
    )

    response = client.post("/api/v1/documents/query", json={})

    assert response.status_code == 200
    fields = {cf["field_id"]: cf for cf in response.json()["items"][0]["custom_fields"]}
    assert fields[42]["kind"] == "present"
    assert fields[42]["monetary"] == {"currency": "EUR", "amount": "450.00"}
    assert fields[43]["kind"] == "absent"
    assert fields[43]["raw"] is None


@respx.mock
def test_custom_field_explicit_null_is_distinct_from_absent(client: TestClient) -> None:
    _mock_reference_endpoints(
        custom_fields=[{"id": 43, "name": "Commentaire", "data_type": "string", "extra_data": None}]
    )
    respx.get(f"{BASE}/api/documents/").mock(
        return_value=Response(
            200,
            json=_page(
                [
                    _document(
                        correspondent=None,
                        document_type=None,
                        tags=[],
                        custom_fields=[{"field": 43, "value": None}],
                    )
                ],
                count=1,
            ),
            headers=V10_HEADERS,
        )
    )

    response = client.post("/api/v1/documents/query", json={})

    assert response.status_code == 200
    field = response.json()["items"][0]["custom_fields"][0]
    assert field["field_id"] == 43
    assert field["kind"] == "null"


@respx.mock
def test_monetary_amount_is_serialized_as_decimal_safe_string(client: TestClient) -> None:
    _mock_reference_endpoints(
        custom_fields=[{"id": 42, "name": "Montant", "data_type": "monetary", "extra_data": None}]
    )
    respx.get(f"{BASE}/api/documents/").mock(
        return_value=Response(
            200,
            json=_page(
                [
                    _document(
                        correspondent=None,
                        document_type=None,
                        tags=[],
                        custom_fields=[{"field": 42, "value": "EUR0.00"}],
                    )
                ],
                count=1,
            ),
            headers=V10_HEADERS,
        )
    )

    response = client.post("/api/v1/documents/query", json={})

    assert response.status_code == 200
    field = response.json()["items"][0]["custom_fields"][0]
    # EUR0.00 must survive as a real, present zero value - never dropped or
    # confused with ABSENT/NULL (M3 brief: preserve the distinction).
    assert field["kind"] == "present"
    assert field["monetary"] == {"currency": "EUR", "amount": "0.00"}


@respx.mock
def test_user_can_change_is_preserved_through_to_the_dto(client: TestClient) -> None:
    _mock_reference_endpoints()
    respx.get(f"{BASE}/api/documents/").mock(
        return_value=Response(
            200,
            json=_page(
                [
                    _document(
                        correspondent=None,
                        document_type=None,
                        tags=[],
                        user_can_change=False,
                    )
                ],
                count=1,
            ),
            headers=V10_HEADERS,
        )
    )

    response = client.post("/api/v1/documents/query", json={})

    assert response.status_code == 200
    assert response.json()["items"][0]["user_can_change"] is False


# --------------------------------------------------------- upstream errors
@respx.mock
def test_paperless_unreachable_maps_to_the_existing_error_envelope(client: TestClient) -> None:
    _mock_reference_endpoints()
    import httpx

    respx.get(f"{BASE}/api/documents/").mock(side_effect=httpx.ConnectError("boom"))

    response = client.post("/api/v1/documents/query", json={})

    assert response.status_code in (502, 503, 504)
    body = response.json()
    assert "error" in body
    assert "test-token-abcdef123456" not in response.text


@respx.mock
def test_paperless_auth_expired_is_mapped_not_leaked_raw(client: TestClient) -> None:
    """A rejected token is PaperWrench's own upstream-connectivity problem.

    VERIFIED_SOURCE (M1): mapped to PaperlessUnauthorizedError, status 502 -
    401 is deliberately not reused here, since that would incorrectly imply
    the *caller of PaperWrench's own API* is unauthenticated, when in fact
    PaperWrench's own credential to Paperless is the one being rejected.
    """
    _mock_reference_endpoints()
    respx.get(f"{BASE}/api/documents/").mock(
        return_value=Response(401, json={"detail": "Invalid token."}, headers=V10_HEADERS)
    )

    response = client.post("/api/v1/documents/query", json={})

    assert response.status_code == 502
    body = response.json()
    assert body["error"]["code"] == "PAPERLESS_UNAUTHORIZED"


# ------------------------------------------------------------- no-write guard
@respx.mock
def test_list_documents_never_issues_a_write_request_to_paperless(client: TestClient) -> None:
    _mock_reference_endpoints(
        tags=[{"id": 1, "name": "Urgent"}],
        correspondents=[{"id": 7, "name": "CH Valenciennes"}],
        document_types=[{"id": 3, "name": "Facture"}],
        custom_fields=[{"id": 42, "name": "Montant", "data_type": "monetary", "extra_data": None}],
    )
    respx.get(f"{BASE}/api/documents/").mock(
        return_value=Response(200, json=_page([_document()], count=1), headers=V10_HEADERS)
    )
    # Any PATCH/POST/DELETE reaching Paperless's documents endpoint is a bug;
    # fail loudly instead of the mock silently 404-ing.
    write_route = respx.route(
        method__in=["PATCH", "POST", "PUT", "DELETE"], host="paperless.test"
    ).mock(return_value=Response(500, json={"detail": "should never be called"}))

    response = client.post(
        "/api/v1/documents/query",
        json={
            "search": {"mode": "title", "text": "vacations"},
            "ordering": "-created",
            "page": 1,
            "page_size": 25,
        },
    )

    assert response.status_code == 200
    assert not write_route.called
