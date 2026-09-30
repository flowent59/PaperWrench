"""Saved rule runs reuse the same preview, job audit and rollback contract."""

from __future__ import annotations

import json
import time

import httpx
import respx
from fastapi.testclient import TestClient

from paperwrench.auth import AuthSession
from paperwrench.db.models import Job
from paperwrench.db.models import JobStatus
from paperwrench.db.models import JobTarget
from paperwrench.db.models import JobType
from paperwrench.db.session import session_scope
from tests.backend.unit.test_previews import BASE
from tests.backend.unit.test_previews import confirmation
from tests.backend.unit.test_previews import create
from tests.backend.unit.test_previews import doc
from tests.backend.unit.test_previews import mock_docs
from tests.backend.unit.test_previews import spec


def wait_for_job(client: TestClient, job_id: int) -> dict[str, object]:
    for _ in range(100):
        job: dict[str, object] = client.get(f"/api/v1/jobs/{job_id}").json()
        if job["status"] in {"completed", "partial", "failed"}:
            return job
        time.sleep(0.01)
    raise AssertionError("Job did not finish")


@respx.mock
def test_rule_preview_equivalence_audit_and_rollback(
    client: TestClient, auth_record: AuthSession
) -> None:
    state = doc(1)
    mock_docs(state)
    respx.get(f"{BASE}1/").mock(side_effect=lambda _: httpx.Response(200, json=state))

    def patch(request: httpx.Request) -> httpx.Response:
        state.update(json.loads(request.content))
        return httpx.Response(200, json=state)

    writes = respx.patch(f"{BASE}1/").mock(side_effect=patch)
    collection = client.post(
        "/api/v1/collections", json={"name": "One", "document_ids": [1]}
    )
    assert collection.status_code == 201, collection.text
    body = {
        "name": "Rename one",
        "definition": {
            "target": {"kind": "collection", "collection_id": collection.json()["id"]},
            "operations": spec()["operations"],
        },
    }
    rule = client.post("/api/v1/rules", json=body)
    assert rule.status_code == 201, rule.text
    assert client.post("/api/v1/rules", json=body).status_code == 409
    rule_id = rule.json()["id"]
    assert len(client.get(f"/api/v1/rules/{rule_id}/revisions").json()) == 1
    equivalent = create(client, spec())
    preview_response = client.post(f"/api/v1/rules/{rule_id}/preview")
    assert preview_response.status_code == 201, preview_response.text
    preview = preview_response.json()
    assert preview["transformation"] == spec()
    assert [preview[key] for key in ("matched", "changed", "errors")] == [
        equivalent[key] for key in ("matched", "changed", "errors")
    ]
    assert not writes.called
    request = {"preview_id": preview["id"], **confirmation(preview, preview["transformation"])}
    assert client.post("/api/v1/jobs", json=request).status_code == 409
    applied = client.post(f"/api/v1/rules/{rule_id}/apply", json=request)
    assert applied.status_code == 201, applied.text
    job_id = applied.json()["id"]
    job = wait_for_job(client, job_id)
    assert job["status"] == "completed"
    assert job["rule_id"] == rule_id and job["rule_revision"] == 1
    assert writes.call_count == 1
    operations = client.get(f"/api/v1/jobs/{job_id}/operations").json()["items"]
    assert operations[0]["written"]["raw"] == "New"
    rollback = client.post(f"/api/v1/jobs/{job_id}/rollback-preview")
    assert rollback.status_code == 201, rollback.text
    staged = rollback.json()
    reverted = client.post(f"/api/v1/jobs/{job_id}/rollback", json={
        "preview_id": staged["id"], "preview_token": staged["preview_token"],
        "target_fingerprint": staged["target_fingerprint"],
        "result_fingerprint": staged["result_fingerprint"],
        "version": 1, "acknowledge": True,
    })
    assert reverted.status_code == 201, reverted.text
    assert wait_for_job(client, reverted.json()["id"])["status"] == "completed"
    assert state["title"] == "Ancien"
    auth_record.paperless_user_id = 2
    assert client.get("/api/v1/rules").json() == []
    assert client.get(f"/api/v1/rules/{rule_id}").status_code == 404


@respx.mock
def test_rule_revision_invalidates_preview(client: TestClient) -> None:
    mock_docs(doc(1))
    collection = client.post(
        "/api/v1/collections", json={"name": "One", "document_ids": [1]}
    ).json()
    body = {
        "name": "Original",
        "definition": {
            "target": {"kind": "collection", "collection_id": collection["id"]},
            "operations": spec()["operations"],
        },
    }
    rule = client.post("/api/v1/rules", json=body).json()
    rule_id = rule["id"]
    preview = client.post(f"/api/v1/rules/{rule_id}/preview").json()
    updated = client.put(f"/api/v1/rules/{rule_id}", json={
        **body, "name": "Updated", "expected_revision": 1,
    })
    assert updated.status_code == 200, updated.text
    assert updated.json()["revision"] == 2
    assert client.put(f"/api/v1/rules/{rule_id}", json={
        **body, "name": "Lost edit", "expected_revision": 1,
    }).status_code == 409
    revisions = client.get(f"/api/v1/rules/{rule_id}/revisions").json()
    assert [item["name"] for item in revisions] == ["Updated", "Original"]
    request = {"preview_id": preview["id"], **confirmation(preview, preview["transformation"])}
    assert client.post(f"/api/v1/rules/{rule_id}/apply", json=request).status_code == 409
    assert client.get("/api/v1/jobs").json()["total"] == 0


@respx.mock
def test_rule_rejects_unfiltered_scope_and_overlapping_job(client: TestClient) -> None:
    state = doc(1)
    mock_docs(state)
    respx.get(f"{BASE}1/").mock(side_effect=lambda _: httpx.Response(200, json=state))
    respx.patch(f"{BASE}1/").mock(side_effect=lambda request: httpx.Response(
        200, json={**state, **json.loads(request.content)}
    ))
    invalid = client.post("/api/v1/rules", json={
        "name": "Everything", "definition": {
            "target": {"kind": "filter", "query": {}},
            "operations": spec()["operations"],
        },
    })
    assert invalid.status_code == 422
    collection = client.post(
        "/api/v1/collections", json={"name": "One", "document_ids": [1]}
    ).json()
    rule = client.post("/api/v1/rules", json={
        "name": "Scoped", "definition": {
            "target": {"kind": "collection", "collection_id": collection["id"]},
            "operations": spec()["operations"],
        },
    }).json()
    preview = client.post(f"/api/v1/rules/{rule['id']}/preview").json()
    request = {"preview_id": preview["id"], **confirmation(preview, preview["transformation"])}
    with session_scope() as db:
        blocker = Job(owner_id=1, type=JobType.TRANSFORM, status=JobStatus.PENDING,
                      title="Existing job")
        db.add(blocker)
        db.flush()
        db.add(JobTarget(job_id=blocker.id, document_id=1, position=0))
        blocker_id = blocker.id
    blocked = client.post(f"/api/v1/rules/{rule['id']}/apply", json=request)
    assert blocked.status_code == 409, blocked.text
    with session_scope() as db:
        blocker = db.get(Job, blocker_id)
        assert blocker is not None
        blocker.status = JobStatus.COMPLETED
    applied = client.post(f"/api/v1/rules/{rule['id']}/apply", json=request)
    assert applied.status_code == 201, applied.text
