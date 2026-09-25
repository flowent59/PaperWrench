"""The HTTP Apply boundary and durable History wire contract."""

import json
import time
from typing import Any

import httpx
import respx
from fastapi.testclient import TestClient

from tests.backend.unit.test_previews import BASE
from tests.backend.unit.test_previews import confirmation
from tests.backend.unit.test_previews import create
from tests.backend.unit.test_previews import doc
from tests.backend.unit.test_previews import mock_docs
from tests.backend.unit.test_previews import spec


@respx.mock
def test_apply_requires_matching_confirmation_and_history_is_paginated(client: TestClient) -> None:
    state = doc(1)
    mock_docs(state)
    respx.get(f"{BASE}1/").mock(side_effect=lambda _: httpx.Response(200, json=state))

    def patch(request: httpx.Request) -> httpx.Response:
        state.update(json.loads(request.content))
        return httpx.Response(200, json=state)

    writes = respx.patch(f"{BASE}1/").mock(side_effect=patch)
    body = spec()
    preview = create(client, body)
    request = {"preview_id": preview["id"], **confirmation(preview, body)}
    assert client.post("/api/v1/jobs", json={}).status_code == 422
    assert client.post("/api/v1/jobs", json={**request, "acknowledge": False}).status_code == 422
    assert (
        client.post("/api/v1/jobs", json={**request, "preview_token": "wrong"}).status_code == 409
    )
    assert not writes.called
    response = client.post("/api/v1/jobs", json=request)
    assert response.status_code == 201, response.text
    assert response.headers["cache-control"] == "no-store"
    job_id = response.json()["id"]
    assert client.post("/api/v1/jobs", json=request).status_code == 409
    final: dict[str, Any] = {}
    for _ in range(100):
        final = client.get(f"/api/v1/jobs/{job_id}").json()
        if final["status"] == "completed":
            break
        time.sleep(0.01)
    assert final["status"] == "completed"
    assert writes.call_count == 1
    history = client.get("/api/v1/jobs?page=1&page_size=25").json()
    assert history["total"] == 1 and len(history["items"]) == 1
    operations = client.get(f"/api/v1/jobs/{job_id}/operations?document_id=1").json()
    assert operations["items"][0]["written"]["raw"] == "New"
    for path in ("", "/targets", "/operations"):
        route = "/api/v1/jobs" if path == "" else f"/api/v1/jobs/{job_id}{path}"
        assert client.get(f"{route}?page_size=100000").status_code == 422
    assert client.post(f"/api/v1/jobs/{job_id}/resume").status_code == 409
    assert client.post(f"/api/v1/jobs/{job_id}/rollback").status_code in (404, 405)
    assert client.get("/api/v1/jobs/99999").status_code == 404
    assert preview["preview_token"] not in json.dumps(history)


@respx.mock
def test_legacy_review_confirmation_cannot_execute(client: TestClient) -> None:
    mock_docs(doc(1))
    body = spec()
    preview = create(client, body)
    request = confirmation(preview, body)
    assert client.post(f"/api/v1/previews/{preview['id']}/confirm", json=request).status_code == 200
    assert (
        client.post("/api/v1/jobs", json={"preview_id": preview["id"], **request}).status_code
        == 409
    )
    assert all(call.request.method == "GET" for call in respx.calls)
