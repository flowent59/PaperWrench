"""M8 safety invariants with real SQLite and a controlled HTTP transport.

These tests prove our behavior, not Paperless's external concurrency semantics.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy import func
from sqlalchemy import select
from sqlalchemy.orm import Session

from paperwrench.config import Settings
from paperwrench.db.engine import get_engine
from paperwrench.db.lock import acquire_lock
from paperwrench.db.models import Job
from paperwrench.db.models import JobOperation
from paperwrench.db.models import JobStatus
from paperwrench.db.models import Preview
from paperwrench.db.models import RuntimeLock
from paperwrench.db.session import session_scope
from paperwrench.errors import PaperWrenchError
from paperwrench.jobs.engine import JobEngine
from paperwrench.jobs.engine import OwnershipLost
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
from tests.backend.unit.test_previews import FIELDS
from tests.backend.unit.test_previews import doc
from tests.backend.unit.test_previews import spec
from tests.backend.unit.test_transformations import core
from tests.backend.unit.test_transformations import custom


@dataclass
class Harness:
    client: PaperlessClient
    registry: MetadataRegistry
    worker: JobEngine
    documents: dict[int, dict[str, Any]]
    requests: list[httpx.Request]
    previews: PreviewService

    async def confirmation(self, body: dict[str, Any] | None = None) -> CreateJob:
        transformation = Transformation.model_validate(body or spec())
        preview = await self.previews.create(transformation, self.client, self.registry)
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

    async def run(self) -> None:
        while target := self.worker._claim():
            await self.worker.execute(*target)


@pytest_asyncio.fixture
async def harness(settings: Settings, session: Session) -> AsyncIterator[Harness]:
    acquire_lock(session, "m8-test")
    documents = {1: doc(1), 2: doc(2)}
    requests: list[httpx.Request] = []

    async def transport(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/custom_fields/":
            return httpx.Response(200, json={"count": len(FIELDS), "next": None, "results": FIELDS})
        if request.url.path == "/api/documents/":
            size = int(request.url.params["page_size"])
            page = int(request.url.params["page"])
            values = list(documents.values())
            return httpx.Response(
                200,
                json={
                    "count": len(values),
                    "next": "next" if page * size < len(values) else None,
                    "results": values[(page - 1) * size : page * size],
                },
            )
        document_id = int(request.url.path.rstrip("/").split("/")[-1])
        if document_id not in documents:
            return httpx.Response(404)
        if request.method == "PATCH":
            documents[document_id].update(json.loads(request.content))
        return httpx.Response(200, json=documents[document_id])

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport), base_url=settings.paperless_url
    ) as http:
        client = PaperlessClient(settings, http_client=http)
        registry = MetadataRegistry(client)
        worker = JobEngine(client, registry, settings, "m8-test")
        yield Harness(client, registry, worker, documents, requests, PreviewService())
        await worker.close()


@pytest.mark.parametrize("boundary", ["insert", "commit"])
async def test_atomic_confirmation_replay_and_failure_before_commit(
    harness: Harness,
    boundary: str,
) -> None:
    request = await harness.confirmation()

    def fail_insert(*args: Any) -> None:
        if "INSERT INTO job_targets" in str(args[2]):
            raise RuntimeError("injected target insert failure")

    engine = get_engine()

    def fail_commit(_session: Session) -> None:
        raise RuntimeError("injected commit failure")

    if boundary == "insert":
        event.listen(engine, "before_cursor_execute", fail_insert)
    else:
        event.listen(Session, "before_commit", fail_commit)
    try:
        with pytest.raises(RuntimeError):
            create_job(request)
    finally:
        if boundary == "insert":
            event.remove(engine, "before_cursor_execute", fail_insert)
        else:
            event.remove(Session, "before_commit", fail_commit)
    with session_scope() as db:
        preview = db.get(Preview, request.preview_id)
        assert preview is not None and not preview.confirmed
        assert db.scalar(select(func.count()).select_from(Job)) == 0
        assert db.scalar(select(func.count()).select_from(JobOperation)) == 0
    job_id = create_job(request)
    with pytest.raises(PaperWrenchError):
        create_job(request)
    assert job_view(job_id).total == 1


async def test_simultaneous_confirmation_creates_one_job(harness: Harness) -> None:
    request = await harness.confirmation()
    results = await asyncio.gather(
        asyncio.to_thread(create_job, request),
        asyncio.to_thread(create_job, request),
        return_exceptions=True,
    )
    assert sum(isinstance(result, int) for result in results) == 1
    assert sum(isinstance(result, PaperWrenchError) for result in results) == 1


async def test_snapshot_multiple_pages_survives_expiry_and_dataset_changes(
    harness: Harness,
) -> None:
    harness.documents.update({i: doc(i) for i in range(3, 107)})
    request = await harness.confirmation(spec(query={}))
    job_id = create_job(request)
    harness.previews.discard(request.preview_id)
    harness.documents[999] = doc(999)
    del harness.documents[1]
    harness.requests.clear()
    await harness.run()
    targets = target_page(job_id, 1, 100, None)
    assert targets.total == 106 and targets.page_count == 2
    assert len(targets.items) == 100
    assert [t.document_id for t in target_page(job_id, 2, 100, None).items] == list(range(101, 107))
    assert all(request.url.path != "/api/documents/" for request in harness.requests)
    assert harness.documents[999]["title"] == "Ancien"
    assert job_view(job_id).status == JobStatus.PARTIAL
    assert job_view(job_id).counts["missing"] == 1


async def test_combined_mutation_fresh_before_readback_normalization_and_neighbors(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness.documents[1]["custom_fields"] = [
        {"field": 1, "value": "Juillet"},
        {"field": 2, "value": "EUR0.00"},
        {"field": 3, "value": False},
        {"field": 99, "value": "unknown retained"},
    ]
    request = await harness.confirmation(
        spec(
            {"operation": "set", "field": core("title"), "value": "  New  "},
            {"operation": "set", "field": custom(1), "value": "Août"},
            {"operation": "set", "field": custom(3), "value": False},
        )
    )
    original = harness.client._request

    async def normalize(method: str, path: str, **kwargs: Any) -> httpx.Response:
        if method == "PATCH":
            kwargs["json"]["title"] = kwargs["json"]["title"].strip()
        return await original(method, path, **kwargs)

    monkeypatch.setattr(harness.client, "_request", normalize)
    job_id = create_job(request)
    harness.requests.clear()
    await harness.run()
    assert [r.method for r in harness.requests if "/documents/" in r.url.path] == [
        "GET",
        "PATCH",
        "GET",
    ]
    assert harness.documents[1]["custom_fields"][1:] == [
        {"field": 2, "value": "EUR0.00"},
        {"field": 3, "value": False},
        {"field": 99, "value": "unknown retained"},
    ]
    operations = operation_page(job_id, 1, 25, 1).items
    title = operations[0]
    assert title.before["raw"] == "Ancien"
    assert title.intended["raw"] == "  New  " and title.written["raw"] == "New"
    assert title.rollback_candidate
    assert operations[2].status == "skipped_unchanged"
    assert operations[2].written is None and not operations[2].rollback_candidate
    assert job_view(job_id).status == JobStatus.COMPLETED


@pytest.mark.parametrize(
    "change,expected",
    [
        ({"title": "External"}, "conflict"),
        ({"user_can_change": False}, "permission"),
        ({"custom_fields": [{"field": 99, "value": "external"}]}, "conflict"),
        ({"title": "New"}, "unchanged"),
    ],
)
async def test_fresh_preconditions_and_already_at_target(
    harness: Harness,
    change: dict[str, Any],
    expected: str,
) -> None:
    job_id = create_job(await harness.confirmation())
    harness.documents[1].update(change)
    await harness.run()
    assert job_view(job_id).counts[expected] == 1
    assert not any(r.method == "PATCH" for r in harness.requests)
    operation = operation_page(job_id, 1, 25, 1).items[0]
    assert operation.written is None and not operation.rollback_candidate


@pytest.mark.parametrize("http_status", [400, 401, 403, 404, 409, 422, 429, 500])
async def test_patch_failures_never_retry(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
    http_status: int,
) -> None:
    job_id = create_job(await harness.confirmation())
    original = harness.client._request
    patches = 0

    async def reject(method: str, path: str, **kwargs: Any) -> httpx.Response:
        nonlocal patches
        if method == "PATCH":
            patches += 1
            response = httpx.Response(http_status, text="secret upstream body")
            harness.client._raise_for_status(response, method, path)
        return await original(method, path, **kwargs)

    monkeypatch.setattr(harness.client, "_request", reject)
    await harness.run()
    assert patches == 1
    operation = operation_page(job_id, 1, 25, 1).items[0]
    assert not operation.rollback_candidate and operation.written is None
    assert operation.http_status == http_status
    assert (operation.status == "ambiguous") == (http_status == 500)
    assert "secret upstream" not in operation.model_dump_json()


class ProcessCrash(BaseException):
    """Bypass application exception handlers like an abrupt process exit."""


@pytest.mark.parametrize("phase", ["before_patch", "after_patch", "readback", "commit"])
async def test_crash_boundaries_recover_without_replay(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    job_id = create_job(await harness.confirmation(spec(ids=[1, 2])))
    original = harness.client._request

    async def crash(method: str, path: str, **kwargs: Any) -> httpx.Response:
        if method == "PATCH" and phase == "before_patch":
            raise ProcessCrash
        response = await original(method, path, **kwargs)
        if method == "PATCH" and phase == "after_patch":
            raise ProcessCrash
        if (
            method == "GET"
            and "/documents/" in path
            and phase == "readback"
            and any(r.method == "PATCH" for r in harness.requests)
        ):
            raise ProcessCrash
        return response

    monkeypatch.setattr(harness.client, "_request", crash)
    target = harness.worker._claim()
    assert target == (job_id, 1)
    original_finish = harness.worker._finish
    if phase == "commit":

        def fail_commit(*args: Any, **kwargs: Any) -> None:
            raise ProcessCrash

        monkeypatch.setattr(harness.worker, "_finish", fail_commit)
    with pytest.raises(ProcessCrash):
        await harness.worker.execute(*target)
    monkeypatch.setattr(harness.worker, "_finish", original_finish)
    monkeypatch.setattr(harness.client, "_request", original)
    recover()
    assert job_view(job_id).status == JobStatus.INTERRUPTED
    assert job_view(job_id).counts["ambiguous"] == 1
    assert job_view(job_id).counts["pending"] == 1
    harness.requests.clear()
    harness.worker.resume(job_id)
    await harness.run()
    assert [r.url.path for r in harness.requests if r.method == "PATCH"] == ["/api/documents/2/"]
    first = operation_page(job_id, 1, 25, 1).items[0]
    assert first.status == "ambiguous" and first.written is None and not first.rollback_candidate
    assert job_view(job_id).status == JobStatus.PARTIAL


async def test_timeout_after_mutation_and_failed_readback_are_ambiguous(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from paperwrench.errors import PaperlessUnreachableError

    job_id = create_job(await harness.confirmation())
    original = harness.client._request

    async def timeout(method: str, path: str, **kwargs: Any) -> httpx.Response:
        response = await original(method, path, **kwargs)
        if method == "PATCH":
            raise PaperlessUnreachableError("timeout")
        return response

    monkeypatch.setattr(harness.client, "_request", timeout)
    await harness.run()
    assert harness.documents[1]["title"] == "New"
    assert job_view(job_id).counts["ambiguous"] == 1
    assert operation_page(job_id, 1, 25, 1).items[0].written is None
    recover()
    assert not job_view(job_id).resumable


async def test_startup_pending_and_reading_require_explicit_resume(harness: Harness) -> None:
    job_id = create_job(await harness.confirmation(spec(ids=[1, 2])))
    assert harness.worker._claim() == (job_id, 1)
    recover()
    assert job_view(job_id).counts["pending"] == 2
    assert harness.worker._claim() is None
    harness.worker.resume(job_id)
    await harness.run()
    assert job_view(job_id).status == JobStatus.COMPLETED


async def test_runtime_loss_before_send_stops_claims(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job_id = create_job(await harness.confirmation(spec(ids=[1, 2])))
    original = harness.client.get_document

    async def lose(document_id: int) -> Any:
        result = await original(document_id)
        with session_scope() as db:
            lock = db.get(RuntimeLock, 1)
            assert lock is not None
            lock.instance_id = "new-owner"
        return result

    monkeypatch.setattr(harness.client, "get_document", lose)
    with pytest.raises(OwnershipLost):
        await harness.run()
    assert not any(r.method == "PATCH" for r in harness.requests)
    assert job_view(job_id).status == JobStatus.INTERRUPTED
    assert job_view(job_id).counts["pending"] == 2


@pytest.mark.parametrize("concurrency", [1, 2, 4])
async def test_fixed_pool_bounds_multiple_jobs_and_same_document(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
    concurrency: int,
) -> None:
    harness.worker.settings = harness.worker.settings.model_copy(
        update={"max_concurrency": concurrency}
    )
    harness.documents.update({i: doc(i) for i in range(3, 21)})
    first = create_job(await harness.confirmation(spec(ids=list(range(1, 21)))))
    second = create_job(await harness.confirmation(spec(ids=list(range(1, 21)))))
    original = harness.client._request
    in_flight = 0
    maximum = 0
    active: set[str] = set()

    async def delayed(method: str, path: str, **kwargs: Any) -> httpx.Response:
        nonlocal in_flight, maximum
        if method == "PATCH":
            assert path not in active
            active.add(path)
            in_flight += 1
            maximum = max(maximum, in_flight)
            await asyncio.sleep(0.005)
        result = await original(method, path, **kwargs)
        if method == "PATCH":
            active.remove(path)
            in_flight -= 1
        return result

    monkeypatch.setattr(harness.client, "_request", delayed)
    harness.worker.start()
    async with asyncio.timeout(15):
        while job_view(second).processed != 20:  # noqa: ASYNC110 - poll the durable public truth
            await asyncio.sleep(0.01)
    assert maximum == concurrency
    assert job_view(first).status == JobStatus.COMPLETED
    assert job_view(second).counts["unchanged"] == 20
    assert len([r for r in harness.requests if r.method == "PATCH"]) == 20


async def test_history_bounds_and_no_secret_persistence(harness: Harness) -> None:
    request = await harness.confirmation()
    job_id = create_job(request)
    await harness.run()
    for size in (0, 1, 100000):
        with pytest.raises(PaperWrenchError):
            operation_page(job_id, 1, size, None)
        with pytest.raises(PaperWrenchError):
            target_page(job_id, 1, size, None)
    with session_scope() as db:
        job = db.get(Job, job_id)
        assert job is not None
        assert request.preview_token not in str(vars(job))
        assert "test-token" not in str(vars(job))
    assert request.preview_token not in job_view(job_id).model_dump_json()


@pytest.mark.parametrize("failure", ["forbidden", "disagreement", "invalid"])
async def test_readback_failure_after_acknowledged_write_never_becomes_definite_rejection(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    from paperwrench.errors import PaperlessForbiddenError

    job_id = create_job(await harness.confirmation())
    original = harness.client.get_document

    async def readback(document_id: int) -> Any:
        current = await original(document_id)
        if any(r.method == "PATCH" for r in harness.requests):
            if failure == "forbidden":
                raise PaperlessForbiddenError("forbidden", details={"upstream_status": 403})
            if failure == "invalid":
                raise ValueError("Malformed upstream response")
            return current.model_copy(update={"title": "external after PATCH"})
        return current

    monkeypatch.setattr(harness.client, "get_document", readback)
    await harness.run()
    operation = operation_page(job_id, 1, 25, 1).items[0]
    assert operation.status == "ambiguous" and operation.written is None
    assert not operation.rollback_candidate


async def test_lock_loss_drains_sent_write_and_stops_later_targets(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job_id = create_job(await harness.confirmation(spec(ids=[1, 2])))
    original = harness.client._request

    async def lose(method: str, path: str, **kwargs: Any) -> httpx.Response:
        response = await original(method, path, **kwargs)
        if method == "PATCH":
            with session_scope() as db:
                lock = db.get(RuntimeLock, 1)
                assert lock is not None
                lock.instance_id = "new-owner"
            harness.worker.stop_scheduling()
        return response

    monkeypatch.setattr(harness.client, "_request", lose)
    target = harness.worker._claim()
    assert target is not None
    await harness.worker.execute(*target)
    assert operation_page(job_id, 1, 25, 1).items[0].rollback_candidate
    assert job_view(job_id).status == JobStatus.INTERRUPTED
    assert job_view(job_id).counts["pending"] == 1
    with pytest.raises(OwnershipLost):
        harness.worker._claim()
    assert len([r for r in harness.requests if r.method == "PATCH"]) == 1


async def test_old_worker_cannot_overwrite_recovery_evidence(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job_id = create_job(await harness.confirmation())
    original = harness.client._request

    async def takeover(method: str, path: str, **kwargs: Any) -> httpx.Response:
        response = await original(method, path, **kwargs)
        if method == "PATCH":
            with session_scope() as db:
                acquire_lock(db, "new-owner", force=True)
            recover()
        return response

    monkeypatch.setattr(harness.client, "_request", takeover)
    target = harness.worker._claim()
    assert target is not None
    await harness.worker.execute(*target)
    assert operation_page(job_id, 1, 25, 1).items[0].status == "ambiguous"
    assert job_view(job_id).status == JobStatus.PARTIAL


async def test_loss_between_targets_interrupts_but_old_scheduler_cannot_stop_resumed_job(
    harness: Harness,
) -> None:
    job_id = create_job(await harness.confirmation(spec(ids=[1, 2])))
    target = harness.worker._claim()
    assert target is not None
    await harness.worker.execute(*target)
    with session_scope() as db:
        acquire_lock(db, "new-owner", force=True)
    harness.worker.stop_scheduling()
    assert job_view(job_id).status == JobStatus.INTERRUPTED
    recover()
    new = JobEngine(harness.client, harness.registry, harness.worker.settings, "new-owner")
    new.resume(job_id)
    harness.worker.stop_scheduling()
    assert job_view(job_id).status == JobStatus.PENDING
    target = new._claim()
    assert target == (job_id, 2)
    await new.execute(*target)
    assert job_view(job_id).status == JobStatus.COMPLETED


async def test_job_and_inspector_share_document_coordinator(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from paperwrench.paperless.errors import PaperlessConflictError
    from paperwrench.paperless.mutations import revision

    original_document = await harness.client.get_document(1)
    job_id = create_job(await harness.confirmation())
    original = harness.client._request
    reached, release = asyncio.Event(), asyncio.Event()

    async def delayed(method: str, path: str, **kwargs: Any) -> httpx.Response:
        if method == "PATCH":
            reached.set()
            await release.wait()
        return await original(method, path, **kwargs)

    monkeypatch.setattr(harness.client, "_request", delayed)
    target = harness.worker._claim()
    assert target is not None
    running = asyncio.create_task(harness.worker.execute(*target))
    await reached.wait()
    inspector = asyncio.create_task(
        harness.client.mutate_document(
            1,
            expected_revision=revision(original_document),
            core={"title": "Inspector"},
            custom_updates=[],
            remove_custom_fields=[],
        )
    )
    await asyncio.sleep(0)
    release.set()
    await running
    with pytest.raises(PaperlessConflictError):
        await inspector
    assert job_view(job_id).status == JobStatus.COMPLETED
    assert harness.documents[1]["title"] == "New"


async def test_metadata_change_and_deleted_document_fail_closed(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from paperwrench.paperless.models import CustomField

    job_id = create_job(await harness.confirmation())

    async def changed_catalog() -> list[CustomField]:
        return []

    original = harness.client.list_custom_fields
    monkeypatch.setattr(harness.client, "list_custom_fields", changed_catalog)
    await harness.run()
    assert job_view(job_id).status == JobStatus.FAILED
    assert job_view(job_id).counts["conflict"] == 1
    monkeypatch.setattr(harness.client, "list_custom_fields", original)
    harness.registry.invalidate()
    second = create_job(await harness.confirmation())
    harness.documents[1]["deleted_at"] = "2026-09-24T10:00:00Z"
    await harness.run()
    assert job_view(second).counts["missing"] == 1
    assert not any(r.method == "PATCH" for r in harness.requests)
