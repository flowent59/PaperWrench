"""Static collection CRUD, bounded membership reads and visibility contract."""

from __future__ import annotations

from pathlib import Path

import respx
from alembic import command
from fastapi.testclient import TestClient
from httpx import Request
from httpx import Response
from sqlalchemy import text

from paperwrench.auth import AuthSession
from paperwrench.db.engine import create_db_engine
from paperwrench.db.engine import get_session_factory
from paperwrench.db.migrate import build_alembic_config
from paperwrench.db.models import CollectionDocument
from paperwrench.paperless.models import Document

BASE = "http://paperless.test"
HEADERS = {"X-Api-Version": "10", "X-Version": "3.1.2"}


def document(document_id: int) -> dict[str, object]:
    return Document(id=document_id, title=f"Vacation {document_id}").model_dump(mode="json")


@respx.mock
def test_collection_crud_pagination_persistence_and_missing(client: TestClient) -> None:
    hidden: set[int] = set()

    def read(request: Request) -> Response:
        document_id = int(request.url.path.rstrip("/").split("/")[-1])
        if document_id in hidden:
            return Response(404, json={"detail": "Not found."}, headers=HEADERS)
        return Response(200, json=document(document_id), headers=HEADERS)

    reads = respx.get(url__regex=rf"{BASE}/api/documents/\d+/").mock(side_effect=read)
    respx.get(f"{BASE}/api/custom_fields/").mock(return_value=Response(
        200, json={"count": 0, "next": None, "previous": None, "results": []}, headers=HEADERS))
    created = client.post("/api/v1/collections", json={
        "name": "Vacations", "document_ids": [3, 1, 3, 2],
    })
    assert created.status_code == 201, created.text
    collection_id = created.json()["id"]
    assert created.json()["member_count"] == 3
    assert client.get("/api/v1/collections").json()[0]["name"] == "Vacations"
    assert client.post("/api/v1/collections", json={"name": "Vacations"}).status_code == 409
    assert client.put(f"/api/v1/collections/{collection_id}", json={
        "name": "Vacation files", "description": "Saved from Explorer",
    }).json()["description"] == "Saved from Explorer"
    assert client.post(f"/api/v1/collections/{collection_id}/documents", json={
        "document_ids": [2, 4, 4],
    }).json()["member_count"] == 4
    hidden.add(2)
    page = client.get(f"/api/v1/collections/{collection_id}/documents?page=1&page_size=2")
    assert page.status_code == 200, page.text
    assert page.json()["total"] == 4
    assert page.json()["page_count"] == 2
    assert [(item["document_id"], item["available"]) for item in page.json()["items"]] == [
        (1, True), (2, False),
    ]
    assert page.json()["items"][1]["document"] is None
    assert reads.call_count == 6  # 3 create + 1 new ID + one page (2)
    assert [item["document_id"] for item in client.get(
        f"/api/v1/collections/{collection_id}/documents?page=2&page_size=2"
    ).json()["items"]] == [3, 4]
    assert client.request("DELETE", f"/api/v1/collections/{collection_id}/documents", json={
        "document_ids": [2, 2],
    }).json()["member_count"] == 3
    with get_session_factory()() as db:
        assert sorted(row.document_id for row in db.query(CollectionDocument).all()) == [1, 3, 4]
    assert client.delete(f"/api/v1/collections/{collection_id}").status_code == 204
    assert client.get(f"/api/v1/collections/{collection_id}").status_code == 404


@respx.mock
def test_invisible_id_rejected_without_metadata_or_write(client: TestClient) -> None:
    respx.get(f"{BASE}/api/documents/99/").mock(return_value=Response(
        403, json={"detail": "secret title"}, headers=HEADERS))
    response = client.post("/api/v1/collections", json={
        "name": "Hidden", "document_ids": [99],
    })
    assert response.status_code == 422
    assert "secret" not in response.text
    assert client.get("/api/v1/collections").json() == []


@respx.mock
def test_dynamic_collection_previews_and_refreshes_without_storing_members(
    client: TestClient, auth_record: AuthSession,
) -> None:
    matching = [document(1), document(2)]

    def list_matching(request: Request) -> Response:
        assert request.url.params["document_type__id"] == "3"
        page_size = int(request.url.params["page_size"])
        return Response(
            200,
            json={
                "count": len(matching),
                "next": None,
                "previous": None,
                "results": matching[:page_size],
            },
            headers=HEADERS,
        )

    respx.get(f"{BASE}/api/documents/").mock(side_effect=list_matching)
    respx.get(f"{BASE}/api/custom_fields/").mock(
        return_value=Response(
            200,
            json={"count": 0, "next": None, "previous": None, "results": []},
            headers=HEADERS,
        )
    )
    respx.get(f"{BASE}/api/document_types/3/").mock(
        return_value=Response(200, json={"id": 3, "name": "Invoice"}, headers=HEADERS)
    )
    filters = {
        "root": {
            "kind": "group",
            "operator": "and",
            "children": [
                {
                    "kind": "condition",
                    "field": {"source": "core", "name": "document_type"},
                    "operator": "equals",
                    "value": 3,
                }
            ],
        }
    }

    preview = client.post("/api/v1/collections/preview", json={"filters": filters})
    assert preview.status_code == 200, preview.text
    assert preview.json()["total"] == 2
    assert [item["document_id"] for item in preview.json()["items"]] == [1, 2]

    created = client.post(
        "/api/v1/collections",
        json={"name": "Invoices", "kind": "dynamic", "filters": filters},
    )
    assert created.status_code == 201, created.text
    collection_id = created.json()["id"]
    assert created.json()["kind"] == "dynamic"
    assert created.json()["member_count"] == 2
    with get_session_factory()() as db:
        assert db.query(CollectionDocument).count() == 0

    matching.append(document(4))
    members = client.get(f"/api/v1/collections/{collection_id}/documents")
    assert members.status_code == 200, members.text
    assert members.json()["total"] == 3
    assert [item["document_id"] for item in members.json()["items"]] == [1, 2, 4]
    assert client.get(f"/api/v1/collections/{collection_id}").json()["member_count"] == 3
    assert client.post(
        f"/api/v1/collections/{collection_id}/documents", json={"document_ids": [9]}
    ).status_code == 409
    auth_record.paperless_user_id = 2
    assert client.get(f"/api/v1/collections/{collection_id}").status_code == 404


def test_collection_migration_persists_membership(tmp_path: Path) -> None:
    url = f"sqlite+pysqlite:///{tmp_path / 'collections.db'}"
    config = build_alembic_config(url)
    command.upgrade(config, "head")
    command.check(config)
    engine = create_db_engine(url)
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO collections (id, name, kind, created_at, updated_at) "
                                "VALUES (1, 'Vacations', 'static', '2026-01-01', '2026-01-01')"))
        connection.execute(text("INSERT INTO collection_documents "
                                "(collection_id, document_id, added_at) "
                                "VALUES (1, 42, '2026-01-01')"))
    engine.dispose()
    reopened = create_db_engine(url)
    with reopened.connect() as connection:
        assert connection.scalar(text("SELECT document_id FROM collection_documents")) == 42
    reopened.dispose()
