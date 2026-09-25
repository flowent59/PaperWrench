"""Guarded M8 real Paperless 3.1.2 writes; fixtures restore/remove sandbox data.

Injected response loss is distinguished from an actual network outage. It proves
the handling of an unknown outcome after a real PATCH, not global atomicity.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy.orm import Session

from paperwrench.config import Settings
from paperwrench.db.engine import get_engine
from paperwrench.db.lock import acquire_lock
from paperwrench.db.session import session_scope
from paperwrench.errors import PaperlessUnreachableError
from paperwrench.jobs.engine import JobEngine
from paperwrench.jobs.engine import recover
from paperwrench.jobs.model import CreateJob
from paperwrench.jobs.store import create_job
from paperwrench.jobs.store import job_view
from paperwrench.jobs.store import operation_page
from paperwrench.jobs.store import target_page
from paperwrench.paperless import MetadataRegistry
from paperwrench.paperless import PaperlessClient
from paperwrench.previews.service import PreviewService
from paperwrench.transformations import Transformation
from tests.backend.unit.test_previews import spec
from tests.backend.unit.test_transformations import core
from tests.backend.unit.test_transformations import custom

pytestmark = pytest.mark.live


async def confirm(client: PaperlessClient, body: dict[str, Any]) -> int:
    transformation = Transformation.model_validate(body)
    previews = PreviewService()
    preview = await previews.create(transformation, client, MetadataRegistry(client))
    assert preview.errors == 0 and preview.changed > 0
    return create_job(
        CreateJob(
            preview_id=preview.id,
            preview_token=preview.preview_token,
            transformation=transformation,
            target_fingerprint=preview.target_fingerprint,
            result_fingerprint=preview.result_fingerprint,
            version=1,
            acknowledge=True,
            acknowledge_external_race=True,
        )
    )


async def execute(client: PaperlessClient, settings: Settings, session: Session) -> JobEngine:
    acquire_lock(session, "m8-live")
    worker = JobEngine(client, MetadataRegistry(client), settings, "m8-live")
    while target := worker._claim():
        await worker.execute(*target)
    return worker


async def test_vacation_dataset_apply_and_history_live(
    live_client: PaperlessClient,
    raw_live: httpx.AsyncClient,
    live_settings: Settings,
    session: Session,
) -> None:
    registry = MetadataRegistry(live_client)
    document_type = await registry.document_type_by_name("Relevé de vacations")
    period = await registry.custom_field_by_name("Période concernée")
    query = {
        "filters": {
            "root": {
                "children": [
                    {
                        "kind": "condition",
                        "field": core("document_type"),
                        "operator": "equals",
                        "value": document_type.id,
                    },
                    {"kind": "condition", "field": custom(period.id), "operator": "has_value"},
                ]
            }
        }
    }
    body = spec(
        {
            "operation": "template",
            "field": core("title"),
            "template": "Relevé de vacations \u2013 {Période concernée}",
            "bindings": {"Période concernée": custom(period.id)},
        },
        query=query,
    )
    # This is only a fixture backup for cleanup, never execution target selection.
    originals = {
        d.id: d
        async for d in live_client.iter_documents(params={"document_type__id": document_type.id})
    }
    assert live_client.paperless_version == "3.1.2"
    try:
        job_id = await confirm(live_client, body)
        await execute(live_client, live_settings, session)
        history = job_view(job_id)
        assert history.status == "completed", history.model_dump_json()
        assert history.processed == history.total > 1
        targets = target_page(job_id, 1, 100, None)
        for target in targets.items:
            current = await live_client.get_document(target.document_id)
            expected = f"Relevé de vacations \u2013 {current.custom_field_map[period.id]}"
            assert current.title == expected
            operation = operation_page(job_id, 1, 25, target.document_id).items[0]
            if target.status == "succeeded":
                assert operation.written["raw"] == current.title
                assert operation.before["raw"] == originals[target.document_id].title
                assert operation.rollback_candidate
            assert current.custom_fields == originals[target.document_id].custom_fields
    finally:
        for document in originals.values():
            response = await raw_live.patch(
                f"/api/documents/{document.id}/", json={"title": document.title}
            )
            response.raise_for_status()


async def test_normalization_and_custom_neighbors_live(
    live_client: PaperlessClient,
    raw_live: httpx.AsyncClient,
    live_settings: Settings,
    session: Session,
    scratch_document: int,
) -> None:
    registry = MetadataRegistry(live_client)
    period = await registry.custom_field_by_name("Période concernée")
    amount = await registry.custom_field_by_name("Montant")
    setup = await raw_live.patch(
        f"/api/documents/{scratch_document}/",
        json={
            "custom_fields": [
                {"field": period.id, "value": "M8 neighbour"},
                {"field": amount.id, "value": "EUR0.00"},
            ],
        },
    )
    setup.raise_for_status()
    body = spec(
        {"operation": "set", "field": core("title"), "value": "  M8 normalized  "},
        {"operation": "set", "field": custom(amount.id), "value": "EUR123.40"},
        ids=[scratch_document],
    )
    job_id = await confirm(live_client, body)
    await execute(live_client, live_settings, session)
    assert job_view(job_id).status == "completed", job_view(job_id).model_dump_json()
    current = await live_client.get_document(scratch_document)
    operations = operation_page(job_id, 1, 25, scratch_document).items
    assert operations[0].intended["raw"] == "  M8 normalized  "
    assert operations[0].written["raw"] == current.title == "M8 normalized"
    assert current.custom_field_map[period.id] == "M8 neighbour"
    assert operations[1].written["raw"] == current.custom_field_map[amount.id] == "EUR123.40"


async def test_conflict_after_preview_and_unknown_result_after_real_patch_live(
    live_client: PaperlessClient,
    raw_live: httpx.AsyncClient,
    live_settings: Settings,
    session: Session,
    scratch_document: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job_id = await confirm(live_client, spec(ids=[scratch_document]))
    changed = await raw_live.patch(
        f"/api/documents/{scratch_document}/", json={"title": "external"}
    )
    changed.raise_for_status()
    await execute(live_client, live_settings, session)
    assert job_view(job_id).counts["conflict"] == 1
    assert (await live_client.get_document(scratch_document)).title == "external"
    second = await confirm(live_client, spec(ids=[scratch_document]))
    original = live_client._request

    async def lose_response(method: str, path: str, **kwargs: Any) -> httpx.Response:
        response = await original(method, path, **kwargs)
        if method == "PATCH":
            raise PaperlessUnreachableError("Injected response loss after real PATCH")
        return response

    monkeypatch.setattr(live_client, "_request", lose_response)
    await execute(live_client, live_settings, session)
    recover()
    assert (await live_client.get_document(scratch_document)).title == "New"
    operation = operation_page(second, 1, 25, scratch_document).items[0]
    assert operation.status == "ambiguous" and operation.written is None
    assert not operation.rollback_candidate and not job_view(second).resumable


async def test_permission_removed_and_document_hidden_after_preview_live(
    raw_live: httpx.AsyncClient,
    live_settings: Settings,
    session: Session,
    scratch_document: int,
    restricted_user: dict[str, object],
) -> None:
    user_id = restricted_user["id"]
    grant = await raw_live.patch(
        f"/api/users/{user_id}/",
        json={
            "user_permissions": ["view_document", "change_document", "view_customfield"],
        },
    )
    grant.raise_for_status()
    path = f"/api/documents/{scratch_document}/"
    share = await raw_live.patch(
        path,
        json={
            "set_permissions": {
                "view": {"users": [user_id], "groups": []},
                "change": {"users": [user_id], "groups": []},
            }
        },
    )
    share.raise_for_status()
    settings = live_settings.model_copy(
        update={"paperless_token": SecretStr(str(restricted_user["token"]))}
    )
    async with PaperlessClient(settings) as restricted:
        job_id = await confirm(restricted, spec(ids=[scratch_document]))
        revoke = await raw_live.patch(
            path,
            json={
                "set_permissions": {
                    "view": {"users": [user_id], "groups": []},
                    "change": {"users": [], "groups": []},
                }
            },
        )
        revoke.raise_for_status()
        await execute(restricted, settings, session)
        assert job_view(job_id).counts["permission"] == 1
        share = await raw_live.patch(
            path,
            json={
                "set_permissions": {
                    "view": {"users": [user_id], "groups": []},
                    "change": {"users": [user_id], "groups": []},
                }
            },
        )
        share.raise_for_status()
        hidden = await confirm(restricted, spec(ids=[scratch_document]))
        hide = await raw_live.patch(
            path,
            json={
                "set_permissions": {
                    "view": {"users": [], "groups": []},
                    "change": {"users": [], "groups": []},
                }
            },
        )
        hide.raise_for_status()
        await execute(restricted, settings, session)
        assert job_view(hidden).counts["missing"] == 1


@pytest.mark.parametrize(
    "phase",
    [
        "before_intent",
        "before_patch",
        "after_patch",
        "after_readback",
        "after_commit",
    ],
)
async def test_real_process_exit_around_live_patch(
    live_client: PaperlessClient,
    live_settings: Settings,
    session: Session,
    scratch_document: int,
    phase: str,
) -> None:
    job_id = await confirm(live_client, spec(ids=[scratch_document]))
    environment = {
        **os.environ,
        "PW_CRASH_URL": live_settings.paperless_url,
        "PW_CRASH_TOKEN": live_settings.paperless_token.get_secret_value(),
    }
    result = await asyncio.to_thread(
        subprocess.run,
        [
            sys.executable,
            "-m",
            "tests.backend.live.job_crash_child",
            str(get_engine().url),
            str(job_id),
            phase,
        ],
        env=environment,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 73, "Crash child did not reach its designated exit boundary"
    with session_scope() as db:
        acquire_lock(db, "m8-live", force=True)
    recover()
    operation = operation_page(job_id, 1, 25, scratch_document).items[0]
    if phase == "before_intent":
        assert job_view(job_id).resumable
        worker = JobEngine(live_client, MetadataRegistry(live_client), live_settings, "m8-live")
        worker.resume(job_id)
        await execute(live_client, live_settings, session)
        assert job_view(job_id).status == "completed"
    elif phase == "after_commit":
        assert job_view(job_id).status == "completed"
        assert operation.rollback_candidate and operation.written["raw"] == "New"
    else:
        assert operation.status == "ambiguous" and operation.written is None
        assert not operation.rollback_candidate and not job_view(job_id).resumable
    # No recovered write was replayed to infer provenance from current == intended.
    if phase in ("after_patch", "after_readback", "after_commit"):
        assert (await live_client.get_document(scratch_document)).title == "New"
