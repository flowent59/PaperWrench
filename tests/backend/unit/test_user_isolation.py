"""Local IDs and durable resources are never shared across Paperless users."""

from __future__ import annotations

from datetime import timedelta
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from paperwrench.auth import AuthSession
from paperwrench.db.base import utcnow
from paperwrench.db.models import DocumentSchema
from paperwrench.db.models import Job
from paperwrench.db.models import JobStatus
from paperwrench.db.models import JobType
from paperwrench.db.models import Preview
from paperwrench.db.session import session_scope
from paperwrench.errors import PaperWrenchError
from paperwrench.previews.model import PreviewSummary


def test_collection_ids_and_names_are_scoped_to_owner(
    client: TestClient, auth_record: AuthSession
) -> None:
    first = client.post(
        "/api/v1/collections", json={"name": "Invoices", "document_ids": []}
    )
    assert first.status_code == 201
    collection_id = first.json()["id"]

    auth_record.paperless_user_id = 2
    assert client.get("/api/v1/collections").json() == []
    assert client.get(f"/api/v1/collections/{collection_id}").status_code == 404
    second = client.post(
        "/api/v1/collections", json={"name": "Invoices", "document_ids": []}
    )
    assert second.status_code == 201
    assert second.json()["id"] != collection_id


def test_schema_job_preview_and_legacy_rows_are_hidden(
    client: TestClient, auth_record: AuthSession
) -> None:
    now = utcnow()
    summary = PreviewSummary(
        id="owned-preview",
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
        db.add_all(
            [
                DocumentSchema(
                    owner_id=1,
                    name="Owned schema",
                    applies_when_json='{"version":1,"query":{}}',
                    rules_json=(
                        '{"version":1,"items":[{"kind":"required","field":'
                        '{"source":"core","name":"title"},"field_type":"text"}]}'
                    ),
                ),
                DocumentSchema(
                    owner_id=None,
                    name="Legacy schema",
                    applies_when_json='{"version":1,"query":{}}',
                    rules_json=(
                        '{"version":1,"items":[{"kind":"required","field":'
                        '{"source":"core","name":"title"},"field_type":"text"}]}'
                    ),
                ),
                Job(
                    owner_id=1,
                    type=JobType.TRANSFORM,
                    status=JobStatus.COMPLETED,
                    title="Owned job",
                ),
                Job(
                    owner_id=None,
                    type=JobType.TRANSFORM,
                    status=JobStatus.COMPLETED,
                    title="Legacy job",
                ),
                Preview(
                    id=summary.id,
                    owner_id=1,
                    expires_at=summary.expires_at,
                    ready=True,
                    token_hash="0" * 64,
                    summary_json=summary.model_dump_json(),
                ),
            ]
        )
        db.flush()
        schema_id = db.query(DocumentSchema.id).filter_by(owner_id=1).scalar()
        job_id = db.query(Job.id).filter_by(owner_id=1).scalar()

    assert [item["name"] for item in client.get("/api/v1/schemas").json()] == [
        "Owned schema"
    ]
    assert [item["title"] for item in client.get("/api/v1/jobs").json()["items"]] == [
        "Owned job"
    ]

    auth_record.paperless_user_id = 2
    assert client.get("/api/v1/schemas").json() == []
    assert client.get(f"/api/v1/schemas/{schema_id}").status_code == 404
    assert client.get("/api/v1/jobs").json()["items"] == []
    assert client.get(f"/api/v1/jobs/{job_id}").status_code == 404
    assert client.get("/api/v1/previews/owned-preview").status_code == 409
    app = cast(FastAPI, client.app)
    with pytest.raises(PaperWrenchError):
        app.state.jobs.bind(
            job_id,
            auth_record.paperless_user_id,
            auth_record.client,
            auth_record.registry,
        )
    assert job_id not in app.state.jobs._credentials
