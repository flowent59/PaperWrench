"""Complete, disjoint workflows against the disposable Paperless instance."""

from __future__ import annotations

from typing import Any
from typing import cast

import pytest
from pydantic import SecretStr
from sqlalchemy.orm import Session

from paperwrench.config import Settings
from paperwrench.db.lock import acquire_lock
from paperwrench.errors import PaperlessForbiddenError
from paperwrench.errors import PaperWrenchError
from paperwrench.jobs.engine import JobEngine
from paperwrench.jobs.model import CreateJob
from paperwrench.jobs.model import CreateRollback
from paperwrench.jobs.rollback import create_rollback
from paperwrench.jobs.store import create_job
from paperwrench.jobs.store import job_view
from paperwrench.paperless import MetadataRegistry
from paperwrench.paperless import PaperlessClient
from paperwrench.paperless import PaperlessNotFoundError
from paperwrench.previews.service import PreviewService
from paperwrench.transformations import Transformation
from tests.backend.unit.test_previews import spec
from tests.backend.unit.test_transformations import core

pytestmark = pytest.mark.live


def _job_confirmation(preview: Any, transformation: Transformation) -> CreateJob:
    return CreateJob(
        preview_id=preview.id,
        preview_token=preview.preview_token,
        transformation=transformation,
        target_fingerprint=preview.target_fingerprint,
        result_fingerprint=preview.result_fingerprint,
        version=1,
        acknowledge=True,
        acknowledge_external_race=True,
    )


def _rollback_confirmation(preview: Any) -> CreateRollback:
    return CreateRollback(
        preview_id=preview.id,
        preview_token=preview.preview_token,
        target_fingerprint=preview.target_fingerprint,
        result_fingerprint=preview.result_fingerprint,
        version=1,
        acknowledge=True,
        acknowledge_external_race=True,
    )


async def _drain(worker: JobEngine) -> None:
    while target := worker._claim():
        await worker.execute(*target)


async def test_each_regular_user_completes_a_disjoint_apply_and_rollback(
    live_settings: Settings,
    session: Session,
    multi_user_library: list[dict[str, object]],
    golden_custom_field_ids: dict[str, int],
) -> None:
    """Exercise explorer/inspector, preview, write, history and rollback twice."""
    acquire_lock(session, "multi-user-live")
    worker = JobEngine(None, None, live_settings, "multi-user-live")
    previews = PreviewService()
    results: list[tuple[int, str, int, int]] = []

    try:
        for actor, other in (
            (multi_user_library[0], multi_user_library[1]),
            (multi_user_library[1], multi_user_library[0]),
        ):
            owner_id = cast(int, actor["id"])
            other_id = cast(int, other["id"])
            document_id = cast(int, actor["document_id"])
            settings = live_settings.model_copy(
                update={"paperless_token": SecretStr(cast(str, actor["token"]))}
            )
            async with PaperlessClient(settings) as client:
                registry = MetadataRegistry(client)

                # Paperless itself enforces the disjoint object permissions:
                # the Explorer sees the owned document, Inspector opens it,
                # and the other user's ID cannot be resolved.
                page = await client.list_documents(page=1, page_size=100)
                visible_ids = {document.id for document in page.results}
                assert document_id in visible_ids
                assert cast(int, other["document_id"]) not in visible_ids
                inspected = await client.get_document(document_id)
                assert inspected.title == actor["title"]
                assert inspected.custom_field_map[golden_custom_field_ids["Montant"]] == (
                    "EUR1.00" if actor["can_delete"] else "EUR2.00"
                )
                with pytest.raises((PaperlessNotFoundError, PaperlessForbiddenError)):
                    await client.get_document(cast(int, other["document_id"]))

                transformation = Transformation.model_validate(
                    spec(
                        {
                            "operation": "set",
                            "field": core("title"),
                            "value": f"Applied by {actor['username']}",
                        },
                        ids=[document_id],
                    )
                )
                staged = await previews.create(
                    transformation, client, registry, owner_id=owner_id
                )
                assert staged.changed == 1 and staged.errors == 0
                with pytest.raises(PaperWrenchError) as hidden_preview:
                    previews.summary(staged.id, other_id)
                assert hidden_preview.value.status_code == 409

                job_id = create_job(
                    _job_confirmation(staged, transformation), owner_id=owner_id
                )
                worker.bind(job_id, owner_id, client, registry)
                await _drain(worker)
                assert job_view(job_id, owner_id).status == "completed"
                assert (await client.get_document(document_id)).title == (
                    f"Applied by {actor['username']}"
                )
                with pytest.raises(PaperWrenchError) as hidden_job:
                    job_view(job_id, other_id)
                assert hidden_job.value.status_code == 404
                with pytest.raises(PaperWrenchError):
                    await previews.create(
                        None,
                        client,
                        registry,
                        rollback_of_job_id=job_id,
                        owner_id=other_id,
                    )

                rollback_preview = await previews.create(
                    None,
                    client,
                    registry,
                    rollback_of_job_id=job_id,
                    owner_id=owner_id,
                )
                rollback_id = create_rollback(
                    job_id, _rollback_confirmation(rollback_preview), owner_id=owner_id
                )
                worker.bind(rollback_id, owner_id, client, registry)
                await _drain(worker)
                assert job_view(rollback_id, owner_id).status == "completed"
                restored = await client.get_document(document_id)
                assert restored.title == actor["title"]
                assert restored.custom_field_map[golden_custom_field_ids["Montant"]] == (
                    "EUR1.00" if actor["can_delete"] else "EUR2.00"
                )
                results.append((owner_id, staged.id, job_id, rollback_id))

        assert results[0][0] != results[1][0]
        assert results[0][1] != results[1][1]
        assert results[0][2:] != results[1][2:]
    finally:
        await worker.close()
