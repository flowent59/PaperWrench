"""M9 on the guarded disposable Paperless 3.1.2 sandbox only."""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy.orm import Session

from paperwrench.config import Settings
from paperwrench.jobs.model import CreateRollback
from paperwrench.jobs.rollback import create_rollback
from paperwrench.jobs.store import job_view
from paperwrench.jobs.store import operation_page
from paperwrench.paperless import MetadataRegistry
from paperwrench.paperless import PaperlessClient
from paperwrench.previews.service import PreviewService
from tests.backend.live.test_jobs_live import confirm
from tests.backend.live.test_jobs_live import execute
from tests.backend.unit.test_previews import spec
from tests.backend.unit.test_transformations import core
from tests.backend.unit.test_transformations import custom

pytestmark = pytest.mark.live


async def rollback(client: PaperlessClient, job_id: int) -> int:
    preview = await PreviewService().create(
        None, client, MetadataRegistry(client), rollback_of_job_id=job_id
    )
    assert preview.changed > 0
    return create_rollback(
        job_id,
        CreateRollback(
            preview_id=preview.id,
            preview_token=preview.preview_token,
            target_fingerprint=preview.target_fingerprint,
            result_fingerprint=preview.result_fingerprint,
            version=1,
            acknowledge=True,
            acknowledge_external_race=True,
        ),
    )


async def test_normalized_job_rollback_preserves_later_neighbors_live(
    live_client: PaperlessClient,
    raw_live: httpx.AsyncClient,
    live_settings: Settings,
    session: Session,
    scratch_document: int,
) -> None:
    registry = MetadataRegistry(live_client)
    period = await registry.custom_field_by_name("Période concernée")
    amount = await registry.custom_field_by_name("Montant")
    path = f"/api/documents/{scratch_document}/"
    response = await raw_live.patch(
        path,
        json={
            "title": "Before M9",
            "custom_fields": [
                {"field": period.id, "value": "neighbor"},
                {"field": amount.id, "value": "EUR0.00"},
            ],
        },
    )
    response.raise_for_status()
    job_id = await confirm(
        live_client,
        spec(
            {"operation": "set", "field": core("title"), "value": "  M9 normalized  "},
            {"operation": "set", "field": custom(amount.id), "value": "EUR123.40"},
            ids=[scratch_document],
        ),
    )
    await execute(live_client, live_settings, session)
    assert live_client.paperless_version == "3.1.2"
    assert job_view(job_id).status == "completed"
    operation = operation_page(job_id, 1, 25, scratch_document).items[0]
    assert operation.intended["raw"] == "  M9 normalized  "
    assert operation.written["raw"] == "M9 normalized"
    # A later edit to an unrelated field must survive preview AND execution.
    rollback_id = await rollback(live_client, job_id)
    response = await raw_live.patch(
        path,
        json={
            "custom_fields": [
                {"field": period.id, "value": "later neighbor"},
                {"field": amount.id, "value": "EUR123.40"},
            ]
        },
    )
    response.raise_for_status()
    await execute(live_client, live_settings, session)
    assert job_view(rollback_id).status == "completed", job_view(rollback_id).model_dump_json()
    current = await live_client.get_document(scratch_document)
    assert current.title == "Before M9"
    assert current.custom_field_map[amount.id] == "EUR0.00"
    assert current.custom_field_map[period.id] == "later neighbor"
    assert job_view(job_id).rollback_job_id == rollback_id
    assert job_view(rollback_id).rollback_of_job_id == job_id


@pytest.mark.parametrize("removed", [False, True])
async def test_rollback_later_title_or_removed_document_live(
    live_client: PaperlessClient,
    raw_live: httpx.AsyncClient,
    live_settings: Settings,
    session: Session,
    scratch_document: int,
    removed: bool,
) -> None:
    job_id = await confirm(live_client, spec(ids=[scratch_document]))
    await execute(live_client, live_settings, session)
    rollback_id = await rollback(live_client, job_id)
    path = f"/api/documents/{scratch_document}/"
    response = (
        await raw_live.delete(path)
        if removed
        else await raw_live.patch(path, json={"title": "Later external title"})
    )
    response.raise_for_status()
    await execute(live_client, live_settings, session)
    assert job_view(rollback_id).counts["missing" if removed else "conflict"] == 1
    operation = operation_page(rollback_id, 1, 25, scratch_document).items[0]
    assert operation.attempts == 0 and operation.written is None
    if not removed:
        assert (await live_client.get_document(scratch_document)).title == "Later external title"
