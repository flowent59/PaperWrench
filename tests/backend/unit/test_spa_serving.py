"""The single-container build serves the SPA and the API from one origin.

These tests pin the routing contract: unknown *frontend* routes fall back to
index.html (client-side routing), while unknown *API* routes must still 404
with the JSON error envelope - never with the HTML shell, which would turn a
backend bug into an unparseable frontend crash.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from paperwrench.config import Settings
from paperwrench.db.engine import dispose_engine

INDEX_HTML = "<!doctype html><html><body><div id='root'></div></body></html>"


@pytest.fixture
def spa_client(db_path: Path, tmp_path: Path) -> Iterator[TestClient]:
    static_dir = tmp_path / "static"
    (static_dir / "assets").mkdir(parents=True)
    (static_dir / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    (static_dir / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")

    settings = Settings(
        PAPERLESS_URL="http://paperless.test",
        PAPERLESS_TOKEN="test-token-abcdef123456",
        PAPERWRENCH_DATABASE_URL=f"sqlite+pysqlite:///{db_path}",
        PAPERWRENCH_STATIC_DIR=str(static_dir),
        PAPERWRENCH_LOG_FORMAT="console",
    )

    from paperwrench.main import create_app

    with TestClient(create_app(settings)) as client:
        yield client
    dispose_engine()


def test_root_serves_the_spa(spa_client: TestClient) -> None:
    response = spa_client.get("/")

    assert response.status_code == 200
    assert "<div id='root'></div>" in response.text


def test_client_side_route_falls_back_to_index(spa_client: TestClient) -> None:
    response = spa_client.get("/transformations/42")

    assert response.status_code == 200
    assert "<div id='root'></div>" in response.text


def test_static_assets_are_served(spa_client: TestClient) -> None:
    response = spa_client.get("/assets/app.js")

    assert response.status_code == 200
    assert "console.log(1)" in response.text


def test_unknown_api_route_never_returns_the_html_shell(spa_client: TestClient) -> None:
    response = spa_client.get("/api/v1/nope")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_api_still_works_when_the_spa_is_mounted(spa_client: TestClient) -> None:
    response = spa_client.get("/api/v1/system/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_path_traversal_is_refused(spa_client: TestClient) -> None:
    response = spa_client.get("/../../etc/passwd")

    assert response.status_code in {200, 400, 404}
    assert "root:" not in response.text
