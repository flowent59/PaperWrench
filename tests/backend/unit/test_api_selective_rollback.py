"""HTTP selection, history and ownership over mocked Paperless documents."""

import json
import time
from typing import Any

import httpx
import respx
from fastapi.testclient import TestClient

from paperwrench.auth.service import AuthSession
from tests.backend.unit.test_previews import BASE
from tests.backend.unit.test_previews import confirmation
from tests.backend.unit.test_previews import create
from tests.backend.unit.test_previews import doc
from tests.backend.unit.test_previews import mock_docs
from tests.backend.unit.test_previews import spec


def terminal(client: TestClient, job_id: int) -> dict[str, Any]:
    for _ in range(100):
        result: dict[str, Any] = client.get(f"/api/v1/jobs/{job_id}").json()
        if result["status"] in {"completed", "partial", "failed"}:
            return result
        time.sleep(0.01)
    raise AssertionError("Job did not finish")


@respx.mock
def test_selected_http_flow_search_history_and_owner(
    client: TestClient, auth_record: AuthSession,
) -> None:
    states = {i: doc(i) for i in range(1, 6)}
    mock_docs(*states.values())
    for index in states:
        respx.get(f"{BASE}{index}/").mock(
            side_effect=lambda _, number=index: httpx.Response(200, json=states[number]),
        )

    def patch(request: httpx.Request) -> httpx.Response:
        index = int(request.url.path.rstrip("/").split("/")[-1])
        states[index].update(json.loads(request.content))
        return httpx.Response(200, json=states[index])

    writes = respx.route(method="PATCH", url__regex=rf"{BASE}\d+/").mock(side_effect=patch)
    transform = spec(ids=list(states))
    preview = create(client, transform)
    original = client.post("/api/v1/jobs", json={
        "preview_id": preview["id"], **confirmation(preview, transform),
    })
    assert original.status_code == 201, original.text
    job_id = original.json()["id"]
    assert terminal(client, job_id)["status"] == "completed"
    assert writes.call_count == 5

    candidates = client.get(f"/api/v1/jobs/{job_id}/rollback-candidates").json()
    assert candidates["total"] == 5
    assert all(row["status"] == "available" for row in candidates["items"])
    searched = client.get(f"/api/v1/jobs/{job_id}/rollback-candidates?search=4").json()
    assert [row["document_id"] for row in searched["items"]] == [4]
    restored_url = f"/api/v1/jobs/{job_id}/rollback-candidates?state=restored"
    assert client.get(restored_url).json()["total"] == 0
    assert client.post(f"/api/v1/jobs/{job_id}/rollback-preview", json={
        "document_ids": [1, 1],
    }).status_code == 422
    assert client.post(f"/api/v1/jobs/{job_id}/rollback-preview", json={
        "document_ids": [999],
    }).status_code == 409
    auth_record.paperless_user_id = 2
    assert client.get(f"/api/v1/jobs/{job_id}/rollback-candidates").status_code == 404
    assert client.get(f"/api/v1/jobs/{job_id}/rollbacks").status_code == 404
    assert client.post(f"/api/v1/jobs/{job_id}/rollback-preview", json={
        "document_ids": [2, 4],
    }).status_code == 404
    auth_record.paperless_user_id = 1

    staged = client.post(f"/api/v1/jobs/{job_id}/rollback-preview", json={
        "document_ids": [2, 4],
    })
    assert staged.status_code == 201, staged.text
    assert staged.json()["changed"] == 2
    body = staged.json()
    rollback = client.post(f"/api/v1/jobs/{job_id}/rollback", json={
        "preview_id": body["id"], "preview_token": body["preview_token"],
        "target_fingerprint": body["target_fingerprint"],
        "result_fingerprint": body["result_fingerprint"],
        "version": 1, "acknowledge": True,
    })
    assert rollback.status_code == 201, rollback.text
    assert terminal(client, rollback.json()["id"])["rollback_counts"] == {
        "selected": 2, "restored": 2, "skipped": 0, "conflicted": 0,
    }
    assert [i for i, state in states.items() if state["title"] == "Ancien"] == [2, 4]
    assert client.get(f"/api/v1/jobs/{job_id}/rollbacks").json()["total"] == 1
    assert client.get(restored_url).json()["total"] == 2
