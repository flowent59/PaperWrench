"""Scheduling contracts: approval, privacy, replay prevention and safe failure."""

import json
from datetime import timedelta
from typing import Any
from typing import cast

import httpx
import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func
from sqlalchemy import select

from paperwrench.auth.service import AuthSession
from paperwrench.db.base import utcnow
from paperwrench.db.models import CollectionDocument
from paperwrench.db.models import Job
from paperwrench.db.models import RuleSchedule
from paperwrench.db.models import SavedRule
from paperwrench.db.models import ScheduleRun
from paperwrench.db.session import session_scope
from paperwrench.jobs import store
from paperwrench.jobs.model import CreateJob
from paperwrench.schedules.engine import ScheduleEngine
from tests.backend.unit.test_previews import BASE
from tests.backend.unit.test_previews import confirmation
from tests.backend.unit.test_previews import doc
from tests.backend.unit.test_previews import mock_docs
from tests.backend.unit.test_previews import spec
from tests.backend.unit.test_saved_rules import wait_for_job


def setup_rule(client: TestClient, auth: AuthSession) -> tuple[int, dict[str, Any]]:
    state = cast(FastAPI, client.app).state
    state.sessions._sessions[auth.session_id] = auth
    respx.get("http://paperless.test/api/profile/").respond(200, json={"id": 1, "username": "test"})
    collection = client.post("/api/v1/collections", json={"name": "Scheduled", "document_ids": [1]})
    response = client.post("/api/v1/rules", json={"name": "Daily", "definition": {
        "target": {"kind": "collection", "collection_id": collection.json()["id"]},
        "operations": spec()["operations"],
    }})
    assert response.status_code == 201, response.text
    rule_id = int(response.json()["id"])
    preview = client.post(f"/api/v1/rules/{rule_id}/preview").json()
    body = {
        "rule_id": rule_id, "acknowledge_unattended": True,
        "recurrence": {"timezone": "Europe/Paris", "frequency": "daily", "hour": 9, "minute": 0},
        "preview": {
            "preview_id": preview["id"], **confirmation(preview, preview["transformation"]),
        },
    }
    return rule_id, body


def approve(client: TestClient, body: dict[str, Any]) -> int:
    response = client.post("/api/v1/schedules", json=body)
    assert response.status_code == 201, response.text
    return int(response.json()["id"])


def due(schedule_id: int) -> None:
    with session_scope() as db:
        row = db.get(RuleSchedule, schedule_id)
        assert row is not None
        row.next_run_at = utcnow() - timedelta(seconds=1)


def tick(client: TestClient) -> None:
    assert client.portal is not None
    client.portal.call(cast(FastAPI, client.app).state.schedules.tick)


@respx.mock
def test_approved_run_is_unique_and_audited(client: TestClient, auth_record: AuthSession) -> None:
    document = doc(1)
    mock_docs(document)
    respx.get(f"{BASE}1/").mock(side_effect=lambda _: httpx.Response(200, json=document))

    def patch(request: httpx.Request) -> httpx.Response:
        document.update(json.loads(request.content))
        return httpx.Response(200, json=document)

    writes = respx.patch(f"{BASE}1/").mock(side_effect=patch)
    rule_id, body = setup_rule(client, auth_record)
    schedule_id = approve(client, body)
    assert not writes.called
    assert client.post("/api/v1/schedules", json=body).status_code == 409
    due(schedule_id)
    tick(client)
    run = client.get(f"/api/v1/schedules/{schedule_id}/runs").json()[0]
    assert wait_for_job(client, run["job_id"])["rule_id"] == rule_id
    tick(client)
    assert writes.call_count == 1
    # A dispatch retry returns the already linked job, even with a consumed preview.
    repeated = store.create_job(
        CreateJob.model_validate(body["preview"]), 1, rule_id, schedule_run_id=run["id"],
    )
    assert repeated == run["job_id"]
    assert client.get(f"/api/v1/schedules/{schedule_id}/runs").json()[0]["status"] == "completed"
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(Job)) == 1
    # Next occurrence reevaluates current documents: no unnecessary second write.
    due(schedule_id)
    tick(client)
    runs = client.get(f"/api/v1/schedules/{schedule_id}/runs").json()
    assert len(runs) == 2 and runs[0]["status"] == "unchanged"
    assert writes.call_count == 1


@respx.mock
def test_ownership_consent_and_disable(client: TestClient, auth_record: AuthSession) -> None:
    mock_docs(doc(1))
    _, body = setup_rule(client, auth_record)
    refused = client.post("/api/v1/schedules", json={**body, "acknowledge_unattended": False})
    assert refused.status_code == 422
    schedule_id = approve(client, body)
    auth_record.paperless_user_id = 2
    assert client.get("/api/v1/schedules").json() == []
    for path in ("disable", "acknowledge"):
        assert client.post(f"/api/v1/schedules/{schedule_id}/{path}").status_code == 404
    assert client.get(f"/api/v1/schedules/{schedule_id}/runs").status_code == 404
    assert client.post("/api/v1/schedules", json=body).status_code == 404
    auth_record.paperless_user_id = 1
    assert not client.post(f"/api/v1/schedules/{schedule_id}/disable").json()["enabled"]
    due(schedule_id)
    tick(client)
    assert client.get(f"/api/v1/schedules/{schedule_id}/runs").json() == []


@pytest.mark.parametrize("failure", ["revision", "expiry", "revoked", "downtime", "permission"])
@respx.mock
def test_failures_never_write(
    client: TestClient, auth_record: AuthSession, failure: str,
) -> None:
    mock_docs(doc(1))
    rule_id, body = setup_rule(client, auth_record)
    schedule_id = approve(client, body)
    if failure == "revision":
        rule = client.get(f"/api/v1/rules/{rule_id}").json()
        updated = client.put(f"/api/v1/rules/{rule_id}", json={
            "name": "Changed", "definition": rule["definition"], "expected_revision": 1,
        })
        assert updated.status_code == 200
    elif failure == "expiry":
        auth_record.expires_at = utcnow() - timedelta(seconds=1)
    elif failure in {"revoked", "downtime"}:
        respx.get("http://paperless.test/api/profile/").respond(
            401 if failure == "revoked" else 503,
        )
    else:
        respx.get(f"{BASE}1/").respond(200, json=doc(1, user_can_change=False))
    due(schedule_id)
    tick(client)
    schedule = client.get("/api/v1/schedules").json()[0]
    assert schedule["notification"]
    assert schedule["enabled"] == (failure == "downtime")
    runs = client.get(f"/api/v1/schedules/{schedule_id}/runs").json()
    assert runs[0]["status"] == "failed" and runs[0]["job_id"] is None
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(Job)) == 0


@respx.mock
def test_revision_change_during_preview_cannot_inherit_approval(
    client: TestClient, auth_record: AuthSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from paperwrench.api.v1 import rules

    mock_docs(doc(1))
    rule_id, body = setup_rule(client, auth_record)
    schedule_id = approve(client, body)
    original = rules.build_rule_preview

    async def changed(*args: Any, **kwargs: Any) -> rules.RuleCreatedPreview:
        with session_scope() as db:
            rule = db.get(SavedRule, rule_id)
            assert rule is not None
            rule.revision += 1
        return await original(*args, **kwargs)

    monkeypatch.setattr(rules, "build_rule_preview", changed)
    due(schedule_id)
    tick(client)
    assert client.get("/api/v1/schedules").json()[0]["notification"] == "PREVIEW_STALE"
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(Job)) == 0


@respx.mock
def test_disable_during_preparation_and_restart(
    client: TestClient, auth_record: AuthSession,
) -> None:
    mock_docs(doc(1))
    _, body = setup_rule(client, auth_record)
    schedule_id = approve(client, body)
    engine: ScheduleEngine = cast(FastAPI, client.app).state.schedules
    due(schedule_id)
    run_id = engine.claim(schedule_id)
    assert run_id is not None
    assert engine.claim(schedule_id) is None
    client.post(f"/api/v1/schedules/{schedule_id}/disable")
    assert client.portal is not None
    client.portal.call(engine.execute, run_id)
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(Job)) == 0
        run = db.get(ScheduleRun, run_id)
        assert run is not None and run.status == "failed"
    # Reapproval survives in SQLite, credentials do not survive engine restart.
    preview = client.post(f"/api/v1/rules/{body['rule_id']}/preview").json()
    body["preview"] = {
        "preview_id": preview["id"], **confirmation(preview, preview["transformation"]),
    }
    approve(client, body)
    client.portal.call(engine.close)
    client.portal.call(engine.start)
    schedule = client.get("/api/v1/schedules").json()[0]
    assert not schedule["enabled"] and schedule["status"] == "needs_approval"


@respx.mock
def test_collection_changes_require_fresh_approval(
    client: TestClient, auth_record: AuthSession,
) -> None:
    mock_docs(doc(1), doc(2))
    rule_id, body = setup_rule(client, auth_record)
    schedule_id = approve(client, body)
    rule = client.get(f"/api/v1/rules/{rule_id}").json()
    collection_id = rule["definition"]["target"]["collection_id"]
    with session_scope() as db:
        db.add(CollectionDocument(collection_id=collection_id, document_id=2))
    due(schedule_id)
    tick(client)
    schedule = client.get("/api/v1/schedules").json()[0]
    assert not schedule["enabled"] and schedule["notification"] == "PREVIEW_STALE"
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(Job)) == 0


@respx.mock
def test_restart_never_replays_claimed_occurrence(
    client: TestClient, auth_record: AuthSession,
) -> None:
    mock_docs(doc(1))
    _, body = setup_rule(client, auth_record)
    schedule_id = approve(client, body)
    engine: ScheduleEngine = cast(FastAPI, client.app).state.schedules
    due(schedule_id)
    run_id = engine.claim(schedule_id)
    assert run_id is not None
    assert client.portal is not None
    client.portal.call(engine.close)
    client.portal.call(engine.start)
    tick(client)
    runs = client.get(f"/api/v1/schedules/{schedule_id}/runs").json()
    assert len(runs) == 1 and runs[0]["status"] == "interrupted"
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(Job)) == 0
