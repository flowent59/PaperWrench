"""M7 contracts against mocked Paperless; these are not live evidence."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import func
from sqlalchemy import select
from sqlalchemy import update

from paperwrench.db.base import utcnow
from paperwrench.db.models import Preview
from paperwrench.db.models import PreviewDocument
from paperwrench.db.session import session_scope
from paperwrench.previews import service
from paperwrench.transformations import Transformation
from tests.backend.unit.test_api_documents import _mock_reference_endpoints
from tests.backend.unit.test_transformations import core
from tests.backend.unit.test_transformations import custom

BASE = "http://paperless.test/api/documents/"
FIELDS = [
    {"id": 1, "name": "Période concernée", "data_type": "string"},
    {"id": 2, "name": "Montant", "data_type": "monetary"},
    {"id": 3, "name": "Validé", "data_type": "boolean"},
    {"id": 4, "name": "Date", "data_type": "date"},
    {
        "id": 5,
        "name": "Choice",
        "data_type": "select",
        "extra_data": {
            "select_options": [{"id": "opaque", "label": "Été"}],
        },
    },
    {"id": 6, "name": "Count", "data_type": "integer"},
]


def doc(document_id: int, **kwargs: Any) -> dict[str, Any]:
    return {"id": document_id, "title": "Ancien", "user_can_change": True, **kwargs}


def spec(
    *operations: dict[str, Any], ids: list[int] | None = None, query: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "targets": (
            {"source": "dataset", "query": query}
            if query is not None
            else {"source": "ids", "document_ids": ids if ids is not None else [1]}
        ),
        "operations": list(operations)
        or [{"operation": "set", "field": core("title"), "value": "New"}],
    }


def mock_docs(*documents: dict[str, Any]) -> None:
    _mock_reference_endpoints(custom_fields=FIELDS)
    for document in documents:
        respx.get(f"{BASE}{document['id']}/").respond(200, json=document)


def create(client: TestClient, body: dict[str, Any]) -> dict[str, Any]:
    response = client.post("/api/v1/previews", json=body)
    assert response.status_code == 201, response.text
    assert response.headers["cache-control"] == "no-store"
    return dict(response.json())


def rows(client: TestClient, preview: dict[str, Any], **params: Any) -> list[dict[str, Any]]:
    response = client.get(f"/api/v1/previews/{preview['id']}/documents", params=params)
    assert response.status_code == 200, response.text
    return list(response.json()["items"])


def confirmation(preview: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    return {
        "preview_token": preview["preview_token"],
        "transformation": body,
        "target_fingerprint": preview["target_fingerprint"],
        "result_fingerprint": preview["result_fingerprint"],
        "version": 1,
        "acknowledge": True,
    }


@respx.mock
def test_mixed_results_m6_unresolved_and_zero_writes(client: TestClient) -> None:
    mock_docs(
        doc(1, custom_fields=[{"field": 1, "value": "Juillet"}]),
        doc(
            2,
            title="Relevé de vacations \u2013 Juillet",
            custom_fields=[{"field": 1, "value": "Juillet"}],
        ),
        doc(3),
        doc(4, user_can_change=False),
    )
    # A catch-all rejects every HTTP mutation, including bulk endpoints.
    writes = respx.route(method__in=["PATCH", "POST", "PUT", "DELETE"]).mock(
        side_effect=AssertionError("Preview attempted an upstream mutation")
    )
    body = spec(
        {
            "operation": "template",
            "field": core("title"),
            "template": "Relevé de vacations \u2013 {Période concernée}",
            "bindings": {"Période concernée": custom(1)},
        },
        ids=[4, 2, 1, 3],
    )
    preview = create(client, body)
    assert [preview[key] for key in ["matched", "evaluated", "changed", "unchanged", "errors"]] == [
        4,
        4,
        1,
        1,
        2,
    ]
    items = rows(client, preview)
    assert [item["document_id"] for item in items] == [1, 2, 3, 4]
    assert items[0]["changes"][0]["intended"]["raw"] == "Relevé de vacations \u2013 Juillet"
    assert items[2]["changes"][0]["issue"]["code"] == "TEMPLATE_UNRESOLVED"
    assert items[3]["issue"]["code"] == "DOCUMENT_NOT_EDITABLE"
    assert len(rows(client, preview, status="error")) == 2
    assert (
        client.post(
            f"/api/v1/previews/{preview['id']}/confirm", json=confirmation(preview, body)
        ).status_code
        == 409
    )
    assert "written_value" not in str(items)
    assert not writes.called
    assert all(call.request.method == "GET" for call in respx.calls)


@pytest.mark.parametrize("value,kind", [("", "present"), (None, "null"), ("ABSENT", "absent")])
@respx.mock
def test_clear_states(client: TestClient, value: Any, kind: str) -> None:
    mock_docs(doc(1, custom_fields=[] if value == "ABSENT" else [{"field": 1, "value": value}]))
    body = spec({"operation": "clear", "field": custom(1), "state": "absent"})
    item = rows(client, create(client, body))[0]
    change = item["changes"][0]
    assert change["before"]["kind"] == kind
    assert change["intended"]["kind"] == "absent"
    assert item["status"] == ("unchanged" if kind == "absent" else "change")


@pytest.mark.parametrize(
    "field,value", [(2, "EUR0.00"), (3, False), (4, "2026-09-24"), (5, "opaque"), (6, 0)]
)
@respx.mock
def test_typed_set_preserves_values(client: TestClient, field: int, value: Any) -> None:
    mock_docs(doc(1, custom_fields=[{"field": field, "value": value}]))
    change = rows(
        client, create(client, spec({"operation": "set", "field": custom(field), "value": value}))
    )[0]["changes"][0]
    assert change["status"] == "unchanged"
    assert change["before"]["raw"] == value
    assert change["intended"]["raw"] == value
    if field == 5:
        assert change["intended"]["select_label"] == "Été"
    if field == 2:
        assert change["intended"]["monetary"] == {"currency": "EUR", "amount": "0.00"}


@respx.mock
def test_replace_and_unknown_metadata_errors_remain_visible(client: TestClient) -> None:
    mock_docs(doc(1, title="Été Été", custom_fields=[{"field": 99, "value": "orphan"}]))
    preview = create(
        client,
        spec(
            {"operation": "replace", "field": core("title"), "find": "Été", "replacement": "Août"},
            {"operation": "set", "field": custom(99), "value": "x"},
        ),
    )
    item = rows(client, preview)[0]
    assert item["status"] == "error"  # Errors take precedence; field proposals remain visible.
    assert item["changes"][0]["intended"]["raw"] == "Août Août"
    assert item["changes"][1]["before"]["raw"] == "orphan"
    assert item["changes"][1]["issue"]["code"] == "UNKNOWN_FIELD"


@pytest.mark.parametrize(
    "status,code", [(404, "DOCUMENT_UNAVAILABLE"), (403, "DOCUMENT_FORBIDDEN")]
)
@respx.mock
def test_missing_invisible_and_forbidden_are_document_errors(
    client: TestClient, status: int, code: str
) -> None:
    mock_docs(doc(2))
    respx.get(f"{BASE}1/").respond(status)
    preview = create(client, spec(ids=[1, 2]))
    assert preview["errors"] == 1 and preview["changed"] == 1
    assert rows(client, preview)[0]["issue"]["code"] == code


@respx.mock
def test_invalid_document_is_partial_but_authentication_is_fatal(client: TestClient) -> None:
    mock_docs(doc(2))
    route = respx.get(f"{BASE}1/").respond(200, json={"id": "invalid"})
    preview = create(client, spec(ids=[1, 2]))
    assert rows(client, preview)[0]["issue"]["code"] == "DOCUMENT_INVALID"
    route.respond(401)
    response = client.post("/api/v1/previews", json=spec(ids=[1, 2]))
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "PAPERLESS_UNAUTHORIZED"
    with session_scope() as session:
        assert session.scalar(select(func.count()).select_from(Preview)) == 1


@pytest.mark.parametrize("ids", [[], [0], [-1], [True], [1, 1], [1.5], ["1"]])
@respx.mock
def test_invalid_target_ids_fail_without_upstream_calls(client: TestClient, ids: list[Any]) -> None:
    assert client.post("/api/v1/previews", json=spec(ids=ids)).status_code == 422
    assert not respx.calls


@respx.mock
def test_noncompilable_filter_never_fetches_documents(client: TestClient) -> None:
    mock_docs()
    body = spec(
        query={
            "filters": {
                "root": {
                    "operator": "or",
                    "children": [
                        {
                            "kind": "condition",
                            "field": core("title"),
                            "operator": "contains",
                            "value": "a",
                        },
                        {
                            "kind": "condition",
                            "field": core("document_type"),
                            "operator": "equals",
                            "value": 1,
                        },
                    ],
                }
            }
        }
    )
    response = client.post("/api/v1/previews", json=body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "FILTER_NOT_COMPILABLE"
    assert all("/documents/" not in str(call.request.url) for call in respx.calls)


@respx.mock
def test_dataset_all_pages_staged_and_paginated_without_refetch(client: TestClient) -> None:
    mock_docs()
    requested: list[int] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        assert request.url.params["page_size"] == "100"
        assert request.url.params["title_search"] == "été"
        assert request.url.params["document_type__id"] == "3"
        assert request.url.params["ordering"] == "title"
        requested.append(page)
        # Prior pages must already be on disk before the next network read.
        with session_scope() as session:
            assert (
                session.scalar(select(func.count()).select_from(PreviewDocument))
                == (page - 1) * 100
            )
        return httpx.Response(
            200,
            json={
                "count": 205,
                "next": "https://evil.invalid/" if page < 3 else None,
                "results": [doc(i) for i in range((page - 1) * 100 + 1, min(page * 100, 205) + 1)],
            },
        )

    respx.get(BASE).mock(side_effect=upstream)
    body = spec(
        query={
            "search": {"mode": "title", "text": "été"},
            "ordering": "title",
            "filters": {
                "root": {
                    "children": [
                        {
                            "kind": "condition",
                            "field": core("document_type"),
                            "operator": "equals",
                            "value": 3,
                        }
                    ]
                }
            },
        }
    )
    preview = create(client, body)
    assert preview["matched"] == preview["changed"] == 205
    assert requested == [1, 2, 3]
    assert [item["document_id"] for item in rows(client, preview, page=9, page_size=25)] == [
        201,
        202,
        203,
        204,
        205,
    ]
    assert requested == [1, 2, 3]
    target = Transformation.model_validate(body).targets
    assert target.source == "dataset"
    assert preview["selection_fingerprint"] == target.query.fingerprint()


@pytest.mark.parametrize("mode", ["duplicate", "count", "short", "empty", "missing"])
@respx.mock
def test_changing_or_broken_pagination_is_fatal_and_cleans_staging(
    client: TestClient, mode: str
) -> None:
    mock_docs()
    respx.get(BASE, params={"page": "1"}).respond(
        200,
        json={
            "count": 2,
            "next": "next",
            "results": [doc(1)],
        },
    )
    route = respx.get(BASE, params={"page": "2"})
    if mode == "missing":
        route.respond(404)
    else:
        route.respond(
            200,
            json={
                "count": 3 if mode == "count" else 2,
                "next": "next" if mode == "empty" else None,
                "results": []
                if mode in {"short", "empty"}
                else [doc(1 if mode == "duplicate" else 2)],
            },
        )
    response = client.post("/api/v1/previews", json=spec(query={}))
    assert response.status_code == 409, response.text
    with session_scope() as session:
        assert session.scalar(select(func.count()).select_from(Preview)) == 0
        assert session.scalar(select(func.count()).select_from(PreviewDocument)) == 0


@respx.mock
def test_fingerprint_determinism_token_confirmation_and_replay(client: TestClient) -> None:
    mock_docs(doc(1), doc(2))
    first = create(client, spec(ids=[2, 1]))
    second = create(client, spec(ids=[1, 2]))
    for key in [
        "spec_fingerprint",
        "selection_fingerprint",
        "target_fingerprint",
        "result_fingerprint",
    ]:
        assert first[key] == second[key]
    assert first["preview_token"] != second["preview_token"]
    request = confirmation(first, spec(ids=[1, 2]))
    url = f"/api/v1/previews/{first['id']}/confirm"
    assert client.post(url, json=request).json()["confirmed"] is True
    assert client.post(url, json=request).status_code == 409
    assert client.post(f"/api/v1/previews/{second['id']}/confirm", json=request).status_code == 409
    assert "preview_token" not in client.get(f"/api/v1/previews/{first['id']}").json()


@pytest.mark.parametrize(
    "change", ["token", "spec", "target", "result", "version", "ack", "ids", "dataset"]
)
@respx.mock
def test_confirmation_binding(client: TestClient, change: str) -> None:
    mock_docs(doc(1))
    body = spec()
    preview = create(client, body)
    request = confirmation(preview, body)
    if change == "token":
        request["preview_token"] += "tampered"
    if change == "spec":
        body["operations"][0]["value"] = "other"
    if change == "ids":
        body["targets"]["document_ids"] = [2]
    if change == "dataset":
        body["targets"] = {"source": "dataset", "query": {}}
    if change == "target":
        request["target_fingerprint"] = "other"
    if change == "result":
        request["result_fingerprint"] = "other"
    if change == "version":
        request["version"] = 2
    if change == "ack":
        request["acknowledge"] = False
    response = client.post(f"/api/v1/previews/{preview['id']}/confirm", json=request)
    assert response.status_code in {409, 422}


@respx.mock
def test_expiry_cleanup_and_discard_cascade(client: TestClient) -> None:
    mock_docs(doc(1))
    preview = create(client, spec())
    with session_scope() as session:
        session.execute(update(Preview).values(expires_at=utcnow() - timedelta(seconds=1)))
    assert client.get(f"/api/v1/previews/{preview['id']}/documents").status_code == 409
    assert (
        client.post(
            f"/api/v1/previews/{preview['id']}/confirm", json=confirmation(preview, spec())
        ).status_code
        == 409
    )
    service.cleanup()
    another = create(client, spec())
    assert client.delete(f"/api/v1/previews/{another['id']}").status_code == 204
    with session_scope() as session:
        assert session.scalar(select(func.count()).select_from(PreviewDocument)) == 0


@respx.mock
def test_limits_never_return_a_truncated_success(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_docs(doc(1), doc(2))
    monkeypatch.setattr(service, "MAX_DOCUMENTS", 1)
    assert client.post("/api/v1/previews", json=spec(ids=[1, 2])).status_code == 422
    monkeypatch.setattr(service, "MAX_BYTES", 1)
    assert client.post("/api/v1/previews", json=spec()).status_code == 422
    with session_scope() as session:
        assert session.scalar(select(func.count()).select_from(Preview)) == 0


@respx.mock
def test_empty_dataset_and_no_apply_route(client: TestClient) -> None:
    mock_docs()
    respx.get(BASE).respond(200, json={"count": 0, "next": None, "results": []})
    preview = create(client, spec(query={}))
    assert preview["evaluated"] == 0
    assert rows(client, preview) == []
    assert (
        client.post(
            f"/api/v1/previews/{preview['id']}/confirm", json=confirmation(preview, spec(query={}))
        ).status_code
        == 409
    )
    assert client.post(f"/api/v1/previews/{preview['id']}/apply").status_code in {404, 405}


@respx.mock
def test_retention_limit_and_restart_cleanup(client: TestClient) -> None:
    mock_docs(doc(1))
    previews = [create(client, spec()) for _ in range(4)]
    response = client.post("/api/v1/previews", json=spec())
    assert response.status_code == 422
    with session_scope() as session:
        session.execute(update(Preview).where(Preview.id == previews[0]["id"]).values(ready=False))
    service.cleanup(startup=True)
    assert client.get(f"/api/v1/previews/{previews[0]['id']}").status_code == 409
    assert client.get(f"/api/v1/previews/{previews[1]['id']}").status_code == 200
    create(client, spec())


@respx.mock
def test_dataset_identity_changes_cannot_reuse_confirmation(client: TestClient) -> None:
    mock_docs()
    respx.get(BASE).respond(200, json={"count": 1, "next": None, "results": [doc(1)]})
    body = spec(query={"ordering": "title"})
    preview = create(client, body)
    changed_queries: list[dict[str, Any]] = [
        {"ordering": "-title"},
        {"search": {"mode": "title", "text": "other"}},
        {
            "filters": {
                "root": {
                    "children": [
                        {
                            "kind": "condition",
                            "field": core("title"),
                            "operator": "contains",
                            "value": "x",
                        }
                    ]
                }
            }
        },
    ]
    for query in changed_queries:
        request = confirmation(preview, spec(query=query))
        response = client.post(f"/api/v1/previews/{preview['id']}/confirm", json=request)
        assert response.status_code == 409
    assert (
        client.post(
            f"/api/v1/previews/{preview['id']}/confirm", json=confirmation(preview, body)
        ).status_code
        == 200
    )
