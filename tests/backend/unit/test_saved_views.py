"""Private CRUD and durable migrations for saved Explorer views."""

from __future__ import annotations

from pathlib import Path

import respx
from alembic import command
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import text

from paperwrench.auth import AuthSession
from paperwrench.db.engine import create_db_engine
from paperwrench.db.migrate import build_alembic_config

BASE = "http://paperless.test"
HEADERS = {"X-Api-Version": "10", "X-Version": "3.1.2"}
EMPTY: dict[str, object] = {"count": 0, "next": None, "previous": None, "results": []}


@respx.mock
def test_saved_view_crud_default_switch_and_private_owner(
    client: TestClient, auth_record: AuthSession
) -> None:
    respx.get(f"{BASE}/api/custom_fields/").mock(
        return_value=Response(200, json=EMPTY, headers=HEADERS)
    )
    definition = {
        "query": {
            "search": {"mode": "advanced", "text": "title:invoice"},
            "filters": {"root": {"kind": "group", "operator": "and", "children": []}},
            "ordering": "-created",
        },
        "page_size": 50,
        "column_visibility": {"title": True, "custom_field_4": False},
    }
    first = client.post(
        "/api/v1/views",
        json={"name": "Invoices", "definition": definition, "is_default": True},
    )
    assert first.status_code == 201, first.text
    assert first.json()["definition"] == definition
    assert first.json()["is_default"] is True

    second = client.post(
        "/api/v1/views",
        json={"name": "Recent", "definition": definition, "is_default": True},
    )
    assert second.status_code == 201, second.text
    assert [row["is_default"] for row in client.get("/api/v1/views").json()] == [False, True]
    view_id = first.json()["id"]
    changed = {**definition, "page_size": 25}
    updated = client.put(
        f"/api/v1/views/{view_id}",
        json={"name": "Invoices revised", "definition": changed, "is_default": False},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["definition"]["page_size"] == 25
    assert client.post(
        "/api/v1/views",
        json={"name": "Invoices revised", "definition": definition},
    ).status_code == 409

    auth_record.paperless_user_id = 2
    assert client.get("/api/v1/views").json() == []
    assert client.delete(f"/api/v1/views/{view_id}").status_code == 404
    assert client.put(
        f"/api/v1/views/{view_id}",
        json={"name": "Leaked", "definition": definition},
    ).status_code == 404


def test_saved_view_migration_creates_and_drops_table(tmp_path: Path) -> None:
    url = f"sqlite+pysqlite:///{tmp_path / 'saved-views.db'}"
    config = build_alembic_config(url)
    command.upgrade(config, "f43a91c02e17")
    command.upgrade(config, "head")
    command.check(config)
    engine = create_db_engine(url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO saved_explorer_views "
                "(owner_id, name, definition_json, is_default, created_at, updated_at) "
                "VALUES (1, 'Invoices', '{}', 1, '2026-01-01', '2026-01-01')"
            )
        )
        assert connection.scalar(
            text("SELECT name FROM saved_explorer_views WHERE owner_id = 1")
        ) == "Invoices"
    engine.dispose()
    command.downgrade(config, "f43a91c02e17")
    engine = create_db_engine(url)
    with engine.connect() as connection:
        assert connection.scalar(
            text("SELECT COUNT(*) FROM sqlite_master WHERE name = 'saved_explorer_views'")
        ) == 0
    engine.dispose()
