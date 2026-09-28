"""Security regression coverage with two real PaperWrench sessions.

Paperless itself is mocked here so failures identify PaperWrench's session and
local-resource boundaries.  Object-permission behaviour against real
Paperless is covered by ``tests/backend/live/test_multi_user_security_live.py``.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from typing import Any
from typing import cast

import httpx
import respx
from fastapi.testclient import TestClient
from pydantic import SecretStr

from paperwrench.db.base import utcnow
from paperwrench.db.models import Job
from paperwrench.db.models import JobStatus
from paperwrench.db.models import JobType
from paperwrench.db.models import Preview
from paperwrench.db.session import session_scope
from paperwrench.previews.model import PreviewSummary

USERS = {
    "alice-secret-token": {
        "id": 101,
        "username": "alice",
        "first_name": "Alice",
        "last_name": "Security",
    },
    "bob-secret-token": {
        "id": 202,
        "username": "bob",
        "first_name": "Bob",
        "last_name": "Security",
    },
}


def _token(request: httpx.Request) -> str:
    return request.headers["Authorization"].removeprefix("Token ")


def _login(client: TestClient, token: str) -> dict[str, Any]:
    response = client.post("/api/v1/auth/login", json={"token": token})
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


def _csrf(session: dict[str, Any]) -> dict[str, str]:
    return {"X-CSRF-Token": str(session["csrf_token"])}


@respx.mock
def test_two_sessions_reject_idor_and_revoke_only_the_affected_identity(
    settings: Any,
) -> None:
    revoked: set[str] = set()

    def profile(request: httpx.Request) -> httpx.Response:
        token = _token(request)
        if token in revoked or token not in USERS:
            return httpx.Response(401)
        return httpx.Response(200, json=USERS[token])

    profile_route = respx.get("http://paperless.test/api/profile/").mock(
        side_effect=profile
    )

    from paperwrench.main import create_app

    app = create_app(settings.model_copy(update={"paperless_token": SecretStr("")}))
    # One lifespan owns SQLite and the worker.  The two clients deliberately
    # have independent cookie jars while sharing that running application.
    with TestClient(app) as owner_a:
        owner_b = TestClient(app)
        try:
            alice = _login(owner_a, "alice-secret-token")
            bob = _login(owner_b, "bob-secret-token")
            assert alice["user_id"] == 101 and bob["user_id"] == 202

            collection = owner_a.post(
                "/api/v1/collections",
                json={"name": "Alice private", "document_ids": []},
                headers=_csrf(alice),
            )
            assert collection.status_code == 201, collection.text
            collection_id = collection.json()["id"]

            now = utcnow()
            preview_id = "a" * 32
            preview_summary = PreviewSummary(
                id=preview_id,
                created_at=now,
                expires_at=now + timedelta(minutes=10),
                matched=0,
                evaluated=0,
                changed=0,
                unchanged=0,
                errors=0,
                selection_fingerprint="selection",
                spec_fingerprint="spec",
                target_fingerprint="targets",
                result_fingerprint="results",
            )
            with session_scope() as db:
                db.add(
                    Preview(
                        id=preview_id,
                        owner_id=101,
                        expires_at=preview_summary.expires_at,
                        ready=True,
                        token_hash="0" * 64,
                        summary_json=preview_summary.model_dump_json(),
                    )
                )
                job = Job(
                    owner_id=101,
                    type=JobType.TRANSFORM,
                    status=JobStatus.COMPLETED,
                    title="Alice private job",
                )
                db.add(job)
                db.flush()
                job_id = job.id

            # Reads and writes return the same non-disclosing responses as an
            # unknown ID.  This includes rollback entry points, not just GETs.
            assert owner_b.get(f"/api/v1/collections/{collection_id}").status_code == 404
            assert owner_b.put(
                f"/api/v1/collections/{collection_id}",
                json={"name": "stolen"},
                headers=_csrf(bob),
            ).status_code == 404
            assert owner_b.delete(
                f"/api/v1/collections/{collection_id}", headers=_csrf(bob)
            ).status_code == 404
            assert owner_a.get(f"/api/v1/collections/{collection_id}").status_code == 200
            assert owner_b.get(f"/api/v1/previews/{preview_id}").status_code == 409
            assert owner_b.delete(
                f"/api/v1/previews/{preview_id}", headers=_csrf(bob)
            ).status_code == 204
            assert owner_a.get(f"/api/v1/previews/{preview_id}").status_code == 200
            assert owner_b.get(f"/api/v1/jobs/{job_id}").status_code == 404
            assert owner_b.get(f"/api/v1/jobs/{job_id}/targets").status_code == 404
            assert owner_b.get(f"/api/v1/jobs/{job_id}/operations").status_code == 404
            assert owner_b.post(
                f"/api/v1/jobs/{job_id}/resume", headers=_csrf(bob)
            ).status_code == 404
            assert owner_b.post(
                f"/api/v1/jobs/{job_id}/rollback-preview", headers=_csrf(bob)
            ).status_code == 404
            assert owner_b.post(
                f"/api/v1/jobs/{job_id}/rollback",
                headers=_csrf(bob),
                json={
                    "preview_id": "b" * 32,
                    "preview_token": "not-alices-preview-token",
                    "target_fingerprint": "targets",
                    "result_fingerprint": "results",
                    "version": 1,
                    "acknowledge": True,
                    "acknowledge_external_race": True,
                },
            ).status_code == 404

            bob_collection = owner_b.post(
                "/api/v1/collections",
                json={"name": "Bob private", "document_ids": []},
                headers=_csrf(bob),
            )
            assert bob_collection.status_code == 201, bob_collection.text

            # Requests overlap in time and retain their own cookie-backed
            # identity.  Neither history nor local collections bleed across.
            with ThreadPoolExecutor(max_workers=2) as executor:
                alice_result = executor.submit(owner_a.get, "/api/v1/collections")
                bob_result = executor.submit(owner_b.get, "/api/v1/collections")
                alice_names = [item["name"] for item in alice_result.result().json()]
                bob_names = [item["name"] for item in bob_result.result().json()]
            assert alice_names == ["Alice private"]
            assert bob_names == ["Bob private"]
            assert [item["title"] for item in owner_a.get("/api/v1/jobs").json()["items"]] == [
                "Alice private job"
            ]
            assert owner_b.get("/api/v1/jobs").json()["items"] == []

            # Revalidation revokes exactly Alice's server-side credential.
            alice_session_id = owner_a.cookies["paperwrench_session"]
            app.state.sessions._sessions[alice_session_id].validated_at = (
                datetime.now(UTC) - timedelta(hours=1)
            )
            revoked.add("alice-secret-token")
            rejected = owner_a.get("/api/v1/auth/me")
            assert rejected.status_code == 401
            assert rejected.json()["error"]["code"] == "AUTH_SESSION_REVOKED"
            assert owner_b.get("/api/v1/auth/me").status_code == 200

            # Credentials never cross the API boundary, including errors and
            # all responses accumulated by the mock transport.
            response_text = " ".join(
                response.text for call in profile_route.calls for response in [call.response]
            )
            assert all(token not in response_text for token in USERS)
        finally:
            owner_b.close()
