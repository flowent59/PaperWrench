"""The two guarantees that make the Filter Engine safe to build on.

**No local fallback.** An expression Paperless cannot answer is refused. It
is never approximated by fetching a wider set and filtering in Python, never
partially applied, never capped. These tests assert that by counting
requests: an uncompilable filter must cost *zero* calls to the documents
endpoint.

**No filter is ever silently ignored.** Paperless-ngx 3.1.2 accepts any
parameter it does not recognise and returns the full, unfiltered result set
with HTTP 200 (VERIFIED_LIVE, M1). So the protection cannot live in the
server's rejection - it has to live in PaperWrench. The mock here is
deliberately built to behave like the worst case: it accepts *everything*
and always answers 200. PaperWrench must still refuse an unknown field or
operator before a request is built.

If either property ever regresses, a transformation's blast radius silently
becomes "the entire library" while every response still looks successful.
That is what these tests exist to prevent.
"""

from __future__ import annotations

import respx
from fastapi.testclient import TestClient
from httpx import Response

from paperwrench.filters import CoreField
from paperwrench.filters import FilterOperator as Op
from paperwrench.filters import FilterSet
from paperwrench.filters import compile_filterset
from paperwrench.filters import validate_filterset
from paperwrench.filters.issues import FilterNotCompilable
from tests.backend.unit.filter_fixtures import MONTANT
from tests.backend.unit.filter_fixtures import PERIODE
from tests.backend.unit.filter_fixtures import catalog
from tests.backend.unit.filter_fixtures import core
from tests.backend.unit.filter_fixtures import custom
from tests.backend.unit.filter_fixtures import or_group

BASE = "http://paperless.test"
V10_HEADERS = {"X-Api-Version": "10", "X-Version": "3.1.2"}
CATALOG = catalog()


def _page(results: list[dict[str, object]]) -> dict[str, object]:
    return {"count": len(results), "next": None, "previous": None, "results": results}


def _permissive_paperless() -> respx.Route:
    """A Paperless that accepts anything, like the real one does.

    Reference endpoints answer normally; the documents endpoint returns 200
    with a full result set **whatever parameters it is given**, exactly as
    3.1.2 does for an unknown filter. Nothing about the response tells
    PaperWrench that its filter was discarded, which is the point.
    """
    for endpoint in ("tags", "correspondents", "document_types", "storage_paths"):
        respx.get(f"{BASE}/api/{endpoint}/").mock(
            return_value=Response(200, json=_page([]), headers=V10_HEADERS)
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
            headers=V10_HEADERS,
        )
    )
    return respx.get(f"{BASE}/api/documents/").mock(
        return_value=Response(
            200,
            json={
                "count": 50_000,
                "next": None,
                "previous": None,
                "results": [
                    {
                        "id": 1,
                        "title": "Every document in the library",
                        "tags": [],
                        "custom_fields": [],
                    }
                ],
            },
            headers=V10_HEADERS,
        )
    )


UNCOMPILABLE = {
    "root": {
        "kind": "group",
        "operator": "or",
        "children": [
            {
                "kind": "condition",
                "field": {"source": "core", "name": "document_type"},
                "operator": "equals",
                "value": 3,
            },
            {
                "kind": "condition",
                "field": {"source": "custom_field", "field_id": MONTANT},
                "operator": "is_missing",
                "value": None,
            },
        ],
    }
}


# =========================================== no request for a refused filter
@respx.mock
def test_an_uncompilable_filter_costs_zero_requests_to_the_documents_endpoint(
    client: TestClient,
) -> None:
    documents = _permissive_paperless()

    response = client.post("/api/v1/documents/query", json={"filters": UNCOMPILABLE})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "FILTER_NOT_COMPILABLE"
    assert documents.call_count == 0, "a refused filter must never reach Paperless"


@respx.mock
def test_counting_an_uncompilable_filter_costs_zero_requests(client: TestClient) -> None:
    documents = _permissive_paperless()

    response = client.post("/api/v1/filters/count", json={"filters": UNCOMPILABLE})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "FILTER_NOT_COMPILABLE"
    assert documents.call_count == 0


@respx.mock
def test_validating_any_filter_costs_zero_requests(client: TestClient) -> None:
    """A builder UI calls /validate on every keystroke. It must be free."""
    documents = _permissive_paperless()

    ok = client.post(
        "/api/v1/filters/validate",
        json={
            "filters": {
                "root": {
                    "kind": "group",
                    "operator": "and",
                    "children": [
                        {
                            "kind": "condition",
                            "field": {"source": "custom_field", "field_id": MONTANT},
                            "operator": "is_missing",
                            "value": None,
                        }
                    ],
                }
            }
        },
    )
    bad = client.post("/api/v1/filters/validate", json={"filters": UNCOMPILABLE})

    assert ok.status_code == 200
    assert bad.status_code == 200
    assert documents.call_count == 0


@respx.mock
def test_a_structurally_invalid_filter_costs_zero_requests(client: TestClient) -> None:
    documents = _permissive_paperless()

    response = client.post(
        "/api/v1/documents/query",
        json={
            "filters": {
                "root": {
                    "kind": "group",
                    "operator": "and",
                    "children": [
                        {
                            "kind": "condition",
                            "field": {"source": "custom_field", "field_id": 999_999},
                            "operator": "equals",
                            "value": "x",
                        }
                    ],
                }
            }
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert documents.call_count == 0


# ================================= a permissive server does not weaken us
@respx.mock
def test_an_unknown_custom_field_is_refused_even_though_paperless_would_accept_it(
    client: TestClient,
) -> None:
    """The mock would happily 200 this. PaperWrench must not let it get there.

    Paperless silently ignores a filter it cannot understand, so "the server
    did not complain" carries no information at all. The refusal has to be
    ours.
    """
    documents = _permissive_paperless()

    response = client.post(
        "/api/v1/documents/query",
        json={
            "filters": {
                "root": {
                    "kind": "group",
                    "children": [
                        {
                            "kind": "condition",
                            "field": {"source": "custom_field", "field_id": 4242},
                            "operator": "contains",
                            "value": "anything",
                        }
                    ],
                }
            }
        },
    )

    assert response.status_code == 422
    assert documents.call_count == 0
    # And the alternative the guard prevents: had the condition been dropped
    # instead, this permissive mock would have answered with the whole library.
    assert response.json()["error"]["details"]["issues"][0]["code"] == "UNKNOWN_FIELD"


@respx.mock
def test_an_unknown_operator_never_reaches_the_wire(client: TestClient) -> None:
    documents = _permissive_paperless()

    response = client.post(
        "/api/v1/documents/query",
        json={
            "filters": {
                "root": {
                    "kind": "group",
                    "children": [
                        {
                            "kind": "condition",
                            "field": {"source": "core", "name": "title"},
                            "operator": "regex_matches",
                            "value": "^scan",
                        }
                    ],
                }
            }
        },
    )

    assert response.status_code == 422
    assert documents.call_count == 0


@respx.mock
def test_an_operator_the_field_type_forbids_never_reaches_the_wire(
    client: TestClient,
) -> None:
    documents = _permissive_paperless()

    response = client.post(
        "/api/v1/documents/query",
        json={
            "filters": {
                "root": {
                    "kind": "group",
                    "children": [
                        {
                            "kind": "condition",
                            "field": {"source": "custom_field", "field_id": MONTANT},
                            "operator": "contains",
                            "value": "0",
                        }
                    ],
                }
            }
        },
    )

    assert response.status_code == 422
    assert documents.call_count == 0


@respx.mock
def test_an_unknown_ordering_still_never_reaches_the_wire(client: TestClient) -> None:
    """The M3 guarantee, re-asserted through the new entry point."""
    documents = _permissive_paperless()

    response = client.post("/api/v1/documents/query", json={"ordering": "sort_by_vibes"})

    assert response.status_code == 422
    assert documents.call_count == 0


# ========================================= the engine itself never fetches
def test_the_compiler_is_pure_and_has_no_way_to_reach_paperless() -> None:
    """No client, no registry, no I/O - by construction, not by convention.

    ``compile_filterset`` takes a FilterSet and a catalogue snapshot and
    returns parameters. There is nothing in its signature that *could*
    perform a request, which is what makes "a refused filter costs nothing"
    a property of the code rather than a promise about the caller.
    """
    import inspect

    signature = inspect.signature(compile_filterset)
    assert list(signature.parameters) == ["filterset", "catalog"]
    assert not inspect.iscoroutinefunction(compile_filterset)
    assert not inspect.iscoroutinefunction(validate_filterset)


def test_refusing_reports_every_reason_so_a_caller_never_retries_blind() -> None:
    filters = FilterSet(
        root=or_group(core(CoreField.TITLE, Op.CONTAINS, "a"), custom(MONTANT, Op.IS_MISSING))
    )
    try:
        compile_filterset(filters, CATALOG)
    except FilterNotCompilable as exc:
        assert exc.issues
        assert exc.details is not None
        assert exc.details["issues"][0]["code"] == "MIXED_OR_UNSUPPORTED"
        assert exc.status_code == 422
    else:  # pragma: no cover
        raise AssertionError("expected FilterNotCompilable")


def test_there_is_no_fetch_all_helper_anywhere_in_the_filter_engine() -> None:
    """A "fetch every match" helper is the fallback this design forbids.

    Its absence is asserted rather than assumed: adding one would be the
    single change that quietly reintroduces the whole failure mode.
    """
    import pkgutil
    from pathlib import Path

    import paperwrench.filters as package

    forbidden = ("iter_documents", "iter_pages", "fetch_all", "list_documents")
    for module_info in pkgutil.iter_modules(package.__path__):
        module = __import__(
            f"paperwrench.filters.{module_info.name}", fromlist=["__file__"]
        )
        text = Path(module.__file__ or "").read_text(encoding="utf-8")
        for name in forbidden:
            assert f"{name}(" not in text, (
                f"paperwrench.filters.{module_info.name} calls {name}(): the Filter "
                "Engine must never fetch documents."
            )
