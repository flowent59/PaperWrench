"""M9 provenance, confirmation and shared execution against controlled HTTP."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from paperwrench.db.base import utcnow
from paperwrench.db.models import JobOperation
from paperwrench.db.models import OperationStatus
from paperwrench.db.models import Preview
from paperwrench.db.models import RuntimeLock
from paperwrench.db.session import session_scope
from paperwrench.errors import PaperlessUnreachableError
from paperwrench.errors import PaperWrenchError
from paperwrench.jobs.engine import OwnershipLost
from paperwrench.jobs.engine import recover
from paperwrench.jobs.model import CreateRollback
from paperwrench.jobs.rollback import create_rollback
from paperwrench.jobs.store import create_job
from paperwrench.jobs.store import job_view
from paperwrench.jobs.store import operation_page
from paperwrench.jobs.store import target_page
from paperwrench.previews.model import CreatedPreview
from tests.backend.unit.test_jobs import Harness
from tests.backend.unit.test_jobs import ProcessCrash
from tests.backend.unit.test_jobs import harness as rollback_harness
from tests.backend.unit.test_previews import doc
from tests.backend.unit.test_previews import spec
from tests.backend.unit.test_transformations import core
from tests.backend.unit.test_transformations import custom

harness = rollback_harness


def confirmation(preview: CreatedPreview) -> CreateRollback:
    return CreateRollback(
        preview_id=preview.id,
        preview_token=preview.preview_token,
        target_fingerprint=preview.target_fingerprint,
        result_fingerprint=preview.result_fingerprint,
        version=1,
        acknowledge=True,
        acknowledge_external_race=True,
    )


async def preview(h: Harness, job_id: int) -> CreatedPreview:
    return await h.previews.create(None, h.client, h.registry, rollback_of_job_id=job_id)


async def original(h: Harness, body: dict[str, Any] | None = None) -> int:
    job_id = create_job(await h.confirmation(body))
    await h.run()
    return job_id


async def test_complete_rollback_links_and_immutable_original(harness: Harness) -> None:
    job_id = await original(harness, spec(ids=[1, 2]))
    evidence = operation_page(job_id, 1, 25, None).model_dump()
    staged = await preview(harness, job_id)
    assert staged.changed == 2
    rollback_id = create_rollback(job_id, confirmation(staged))
    harness.requests.clear()
    await harness.run()
    assert job_view(rollback_id).status == "completed"
    assert job_view(rollback_id).rollback_of_job_id == job_id
    assert job_view(job_id).rollback_job_id == rollback_id
    assert operation_page(job_id, 1, 25, None).model_dump() == evidence
    for operation in operation_page(rollback_id, 1, 25, None).items:
        assert operation.before["raw"] == "New"
        assert operation.intended["raw"] == operation.written["raw"] == "Ancien"
        assert operation.rollback_of_operation_id is not None
    assert len([r for r in harness.requests if r.method == "PATCH"]) == 2


@pytest.mark.parametrize(
    "field_id,before,after",
    [
        (1, "ABSENT", "value"),
        (1, None, "value"),
        (1, "", "value"),
        (6, 0, 42),
        (3, False, True),
        (2, "EUR0.00", "EUR123.40"),
        (5, "opaque", None),
        (4, "2026-01-02", "2026-09-25"),
        (1, "value", "ABSENT"),
        (1, "value", None),
    ],
)
async def test_typed_restoration_and_latest_neighbors(
    harness: Harness, field_id: int, before: Any, after: Any
) -> None:
    harness.documents[1]["custom_fields"] = [{"field": 99, "value": "old neighbor"}]
    if before != "ABSENT":
        harness.documents[1]["custom_fields"].append({"field": field_id, "value": before})
    operation = {"field": custom(field_id), "operation": "set", "value": after}
    if after is None or after == "ABSENT":
        operation = {
            "field": custom(field_id),
            "operation": "clear",
            "state": "absent" if after == "ABSENT" else "null",
        }
    job_id = await original(harness, spec(operation))
    staged = await preview(harness, job_id)
    harness.documents[1]["custom_fields"][0]["value"] = "later neighbor"
    rollback_id = create_rollback(job_id, confirmation(staged))
    harness.requests.clear()
    await harness.run()
    assert job_view(rollback_id).status == "completed"
    fields = {entry["field"]: entry["value"] for entry in harness.documents[1]["custom_fields"]}
    assert fields[99] == "later neighbor"
    if before == "ABSENT":
        assert field_id not in fields
    else:
        assert type(fields[field_id]) is type(before) and fields[field_id] == before
    assert len([r for r in harness.requests if r.method == "PATCH"]) == 1


@pytest.mark.parametrize(
    "change,outcome",
    [
        ({"title": "external"}, "conflict"),
        ({"title": "Ancien"}, "conflict"),
        ({"user_can_change": False}, "permission"),
        ({"deleted_at": "2026-09-25"}, "missing"),
        (None, "missing"),
    ],
)
async def test_execution_rechecks_and_never_overwrites(
    harness: Harness, change: dict[str, Any] | None, outcome: str
) -> None:
    job_id = await original(
        harness,
        spec(
            {"operation": "set", "field": core("title"), "value": "New"},
            {"operation": "set", "field": custom(1), "value": "added"},
        ),
    )
    staged = await preview(harness, job_id)
    rollback_id = create_rollback(job_id, confirmation(staged))
    if change is None:
        del harness.documents[1]
    else:
        harness.documents[1].update(change)
    harness.requests.clear()
    await harness.run()
    assert job_view(rollback_id).counts[outcome] == 1
    assert not any(r.method == "PATCH" for r in harness.requests)
    assert all(o.written is None for o in operation_page(rollback_id, 1, 25, None).items)


async def test_partial_job_excludes_unproven_writes_and_preview_conflicts(harness: Harness) -> None:
    harness.documents.update({i: doc(i) for i in range(3, 6)})
    job_id = create_job(await harness.confirmation(spec(ids=[1, 2, 3, 4, 5])))
    harness.documents[2]["title"] = "external"
    harness.documents[3]["title"] = "New"  # Already at target: no provenance.
    await harness.run()
    with session_scope() as session:
        operation = session.scalar(
            select(JobOperation).where(JobOperation.job_id == job_id, JobOperation.document_id == 4)
        )
        assert operation is not None
        operation.status = OperationStatus.AMBIGUOUS
    harness.documents[5]["title"] = "later edit"
    staged = await preview(harness, job_id)
    assert (staged.changed, staged.unchanged, staged.errors) == (1, 2, 2)
    rollback_id = create_rollback(job_id, confirmation(staged))
    harness.requests.clear()
    await harness.run()
    assert job_view(rollback_id).status == "partial"
    assert [r.url.path for r in harness.requests if r.method == "PATCH"] == ["/api/documents/1/"]
    assert harness.documents[4]["title"] == "New"
    assert harness.documents[5]["title"] == "later edit"


@pytest.mark.parametrize("tamper", ["token", "results", "targets", "job", "expiry", "race"])
async def test_confirmation_fails_closed(harness: Harness, tamper: str) -> None:
    job_id = await original(harness, spec({"operation": "set", "field": custom(1), "value": "new"}))
    staged = await preview(harness, job_id)
    request = confirmation(staged)
    if tamper == "expiry":
        with session_scope() as session:
            saved = session.get(Preview, staged.id)
            assert saved is not None
            saved.expires_at = utcnow() - timedelta(seconds=1)
    elif tamper == "job":
        job_id = await original(harness, spec(ids=[2]))
    else:
        key = {
            "token": "preview_token",
            "results": "result_fingerprint",
            "targets": "target_fingerprint",
            "race": "acknowledge_external_race",
        }[tamper]
        request = request.model_copy(update={key: False if tamper == "race" else "wrong"})
    harness.requests.clear()
    with pytest.raises(PaperWrenchError):
        create_rollback(job_id, request)
    assert not harness.requests


async def test_duplicate_requests_and_distinct_previews_create_one_job(harness: Harness) -> None:
    job_id = await original(harness)
    first, second = await preview(harness, job_id), await preview(harness, job_id)
    results = await asyncio.gather(
        *[asyncio.to_thread(create_rollback, job_id, confirmation(p)) for p in (first, second)],
        return_exceptions=True,
    )
    assert sum(isinstance(result, int) for result in results) == 1
    assert sum(isinstance(result, PaperWrenchError) for result in results) == 1
    with pytest.raises(PaperWrenchError):
        create_rollback(job_id, confirmation(first))


@pytest.mark.parametrize("phase", ["before_patch", "after_patch", "readback", "commit", "timeout"])
async def test_rollback_crash_ambiguity_recovery(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    job_id = await original(harness, spec(ids=[1, 2]))
    rollback_id = create_rollback(job_id, confirmation(await preview(harness, job_id)))
    original_request = harness.client._request
    patched = False

    async def crash(method: str, path: str, **kwargs: Any) -> httpx.Response:
        nonlocal patched
        if method == "PATCH" and phase == "before_patch":
            raise ProcessCrash
        if method == "GET" and patched and phase == "readback":
            raise ProcessCrash
        response = await original_request(method, path, **kwargs)
        if method == "PATCH":
            patched = True
            if phase == "after_patch":
                raise ProcessCrash
            if phase == "timeout":
                raise PaperlessUnreachableError("secret upstream body")
        return response

    def fail_commit(*args: Any, **kwargs: Any) -> None:
        raise ProcessCrash

    with monkeypatch.context() as patch:
        patch.setattr(harness.client, "_request", crash)
        if phase == "commit":
            patch.setattr(harness.worker, "_finish", fail_commit)
        target = harness.worker._claim()
        assert target is not None
        if phase == "timeout":
            await harness.worker.execute(*target)
        else:
            with pytest.raises(ProcessCrash):
                await harness.worker.execute(*target)
    recover()
    assert job_view(rollback_id).counts["ambiguous"] == 1
    operation = operation_page(rollback_id, 1, 25, 1).items[0]
    assert operation.written is None and not operation.rollback_candidate
    assert "secret" not in operation.model_dump_json()
    harness.requests.clear()
    harness.worker.resume(rollback_id)
    await harness.run()
    assert [r.url.path for r in harness.requests if r.method == "PATCH"] == ["/api/documents/2/"]


async def test_rollback_runtime_loss_before_send(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = await original(harness)
    rollback_id = create_rollback(job_id, confirmation(await preview(harness, job_id)))
    get = harness.client.get_document

    async def lose(document_id: int) -> Any:
        result = await get(document_id)
        with session_scope() as session:
            lock = session.get(RuntimeLock, 1)
            assert lock is not None
            lock.instance_id = "successor"
        return result

    monkeypatch.setattr(harness.client, "get_document", lose)
    harness.requests.clear()
    with pytest.raises(OwnershipLost):
        await harness.run()
    assert job_view(rollback_id).status == "interrupted"
    assert not any(r.method == "PATCH" for r in harness.requests)


async def test_rollback_pages_and_no_secret(harness: Harness) -> None:
    harness.documents.update({i: doc(i) for i in range(1, 104)})
    job_id = await original(harness, spec(ids=list(harness.documents)))
    staged = await preview(harness, job_id)
    assert staged.evaluated == 103
    page = harness.previews.page(staged.id, 5, 25)
    assert len(page.items) == 3 and page.page_count == 5
    rollback_id = create_rollback(job_id, confirmation(staged))
    history = target_page(rollback_id, 5, 25, None)
    assert history.total == 103 and len(history.items) == 3
    assert staged.preview_token not in job_view(rollback_id).model_dump_json()


@pytest.mark.parametrize("status", ["ambiguous", "skipped_unchanged", "legacy"])
async def test_no_automatic_rollback_without_provenance(harness: Harness, status: str) -> None:
    job_id = await original(harness)
    with session_scope() as session:
        operation = session.scalar(select(JobOperation).where(JobOperation.job_id == job_id))
        assert operation is not None
        if status == "legacy":
            operation.before_value_json = '"old scalar evidence"'
        else:
            operation.status = OperationStatus(status)
    staged = await preview(harness, job_id)
    assert staged.changed == 0
    with pytest.raises(PaperWrenchError):
        create_rollback(job_id, confirmation(staged))


async def test_normalization_compares_written_and_groups_fields(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = harness.client._request

    async def normalize(method: str, path: str, **kwargs: Any) -> httpx.Response:
        if method == "PATCH" and "title" in kwargs["json"]:
            kwargs["json"]["title"] = kwargs["json"]["title"].strip()
        return await request(method, path, **kwargs)

    monkeypatch.setattr(harness.client, "_request", normalize)
    job_id = await original(
        harness,
        spec(
            {"operation": "set", "field": core("title"), "value": "  New  "},
            {"operation": "set", "field": custom(1), "value": "added"},
            {"operation": "clear", "field": core("correspondent")},
        ),
    )
    staged = await preview(harness, job_id)
    assert staged.changed == 1
    rollback_id = create_rollback(job_id, confirmation(staged))
    harness.requests.clear()
    await harness.run()
    assert job_view(rollback_id).status == "completed"
    assert len(operation_page(rollback_id, 1, 25, None).items) == 2
    assert len([r for r in harness.requests if r.method == "PATCH"]) == 1
    assert harness.documents[1]["title"] == "Ancien"
    assert harness.documents[1]["custom_fields"] == []


async def test_rollback_readback_disagreement_is_ambiguous(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = await original(harness)
    rollback_id = create_rollback(job_id, confirmation(await preview(harness, job_id)))
    request = harness.client._request

    async def interleave(method: str, path: str, **kwargs: Any) -> httpx.Response:
        response = await request(method, path, **kwargs)
        if method == "PATCH":
            harness.documents[1]["title"] = "external after PATCH"
        return response

    monkeypatch.setattr(harness.client, "_request", interleave)
    await harness.run()
    operation = operation_page(rollback_id, 1, 25, None).items[0]
    assert operation.status == "ambiguous" and operation.written is None
    assert operation.error == "READBACK_DISAGREES"
