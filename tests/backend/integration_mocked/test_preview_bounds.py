"""Synthetic load and lazy iteration. No claims about live 10k capacity."""

from __future__ import annotations

import asyncio
import gc
import time
import weakref
from pathlib import Path

import httpx
import pytest
from sqlalchemy import event
from sqlalchemy import func
from sqlalchemy import select
from sqlalchemy.orm import Session

from paperwrench.config import Settings
from paperwrench.db.models import Preview
from paperwrench.db.models import PreviewDocument
from paperwrench.db.session import session_scope
from paperwrench.errors import PaperWrenchError
from paperwrench.jobs.model import CreateJob
from paperwrench.jobs.store import create_job
from paperwrench.jobs.store import operation_page
from paperwrench.jobs.store import target_page
from paperwrench.paperless import MetadataRegistry
from paperwrench.paperless import PaperlessClient
from paperwrench.paperless.models import CustomField
from paperwrench.paperless.models import Document
from paperwrench.previews import service
from paperwrench.previews.service import PreviewService
from paperwrench.transformations import Transformation
from paperwrench.transformations import evaluate
from paperwrench.transformations.model import EvaluationResult


def spec() -> Transformation:
    return Transformation.model_validate(
        {
            "targets": {"source": "dataset", "query": {}},
            "operations": [
                {"operation": "set", "field": {"source": "core", "name": "title"}, "value": "New"}
            ],
        }
    )


async def test_iterator_does_not_fetch_next_page_until_consumed(settings: Settings) -> None:
    calls: list[int] = []

    def transport(request: httpx.Request) -> httpx.Response:
        calls.append(int(request.url.params["page"]))
        assert request.method == "GET"
        return httpx.Response(
            200,
            json={
                "count": 10000,
                "next": "next",
                "results": [{"id": 1, "title": "One"}, {"id": 2, "title": "Two"}],
            },
        )

    async with httpx.AsyncClient(
        base_url=settings.paperless_url, transport=httpx.MockTransport(transport)
    ) as http:
        client = PaperlessClient(settings, http_client=http)
        stream = client.iter_documents(page_size=2)
        assert calls == []
        assert (await anext(stream)).id == 1
        assert calls == [1]
        assert (await anext(stream)).id == 2
        assert calls == [1]
        await stream.aclose()
        assert calls == [1]


@pytest.mark.parametrize("target_count", [10_000, 100_000])
async def test_large_target_documents_release_prior_pages(
    settings: Settings,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
    target_count: int,
    db_path: Path,
) -> None:
    page_calls = 0
    previous: list[weakref.ReferenceType[Document]] = []

    def tracked(
        document: Document, transformation: Transformation, fields: dict[int, CustomField]
    ) -> EvaluationResult:
        previous.append(weakref.ref(document))
        return evaluate(document, transformation, fields)

    monkeypatch.setattr(service, "evaluate", tracked)

    def transport(request: httpx.Request) -> httpx.Response:
        nonlocal page_calls
        assert request.method == "GET"
        if request.url.path == "/api/custom_fields/":
            return httpx.Response(200, json={"count": 0, "next": None, "results": []})
        page_calls += 1
        page = int(request.url.params["page"])
        assert int(request.url.params["page_size"]) == 100
        gc.collect()
        # At request time, at most the last complete page is still referenced.
        assert sum(ref() is not None for ref in previous) <= 100
        previous[:] = [ref for ref in previous if ref() is not None]
        with session_scope() as db:
            assert db.scalar(select(func.count()).select_from(PreviewDocument)) == (page - 1) * 100
        return httpx.Response(
            200,
            json={
                "count": target_count,
                "next": "next" if page < target_count // 100 else None,
                "results": [
                    {"id": i, "title": "Old", "user_can_change": True}
                    for i in range((page - 1) * 100 + 1, page * 100 + 1)
                ],
            },
        )

    async with httpx.AsyncClient(
        base_url=settings.paperless_url, transport=httpx.MockTransport(transport)
    ) as http:
        client = PaperlessClient(settings, http_client=http)
        previews = PreviewService()
        preview_started = time.monotonic()
        preview = await previews.create(spec(), client, MetadataRegistry(client))
        preview_seconds = time.monotonic() - preview_started
        assert preview.matched == preview.changed == target_count
        assert page_calls == target_count // 100
        last = previews.page(preview.id, target_count // 25, 25)
        assert last.items[-1].document_id == target_count
        assert len(last.items) == 25
        gc.collect()
        assert all(ref() is None for ref in previous)
        batch_sizes: list[int] = []

        def observe(db: Session, *_args: object) -> None:
            batch_sizes.append(len(db.new))

        event.listen(Session, "before_flush", observe)
        started = time.monotonic()
        try:
            job_id = create_job(
                CreateJob(
                    preview_id=preview.id,
                    preview_token=preview.preview_token,
                    transformation=spec(),
                    target_fingerprint=preview.target_fingerprint,
                    result_fingerprint=preview.result_fingerprint,
                    version=1,
                    acknowledge=True,
                )
            )
        finally:
            event.remove(Session, "before_flush", observe)
        adoption_seconds = time.monotonic() - started
        started = time.monotonic()
        assert max(batch_sizes) <= 200  # 100 targets + 100 field audit rows
        assert page_calls == target_count // 100  # Job creation performs no fresh selection HTTP.
        targets = target_page(job_id, target_count // 25, 25, None)
        assert targets.items[-1].document_id == target_count
        assert len(operation_page(job_id, target_count // 25, 25, None).items) == 25
        print(
            f"Synthetic targets={target_count} pages={page_calls} "
            f"preview={preview_seconds:.2f}s adoption={adoption_seconds:.2f}s "
            f"history_pages={time.monotonic() - started:.3f}s "
            f"db_bytes={sum(p.stat().st_size for p in db_path.parent.glob('test.db*'))}"
        )


async def test_cancellation_removes_staging_and_releases_builder(
    settings: Settings,
    session: Session,
) -> None:
    reached = asyncio.Event()

    async def transport(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/custom_fields/":
            return httpx.Response(200, json={"count": 0, "next": None, "results": []})
        reached.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async with httpx.AsyncClient(
        base_url=settings.paperless_url, transport=httpx.MockTransport(transport)
    ) as http:
        client = PaperlessClient(settings, http_client=http)
        previews = PreviewService()
        registry = MetadataRegistry(client)
        task = asyncio.create_task(previews.create(spec(), client, registry))
        await reached.wait()
        with pytest.raises(PaperWrenchError, match="already building"):
            await previews.create(spec(), client, registry)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not previews._building
        with session_scope() as db:
            assert db.scalar(select(func.count()).select_from(Preview)) == 0


async def test_build_timeout_cleans_up(
    settings: Settings, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service, "TIMEOUT_SECONDS", 0.01)

    async def transport(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/custom_fields/":
            return httpx.Response(200, json={"count": 0, "next": None, "results": []})
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async with httpx.AsyncClient(
        base_url=settings.paperless_url, transport=httpx.MockTransport(transport)
    ) as http:
        client = PaperlessClient(settings, http_client=http)
        previews = PreviewService()
        with pytest.raises(PaperWrenchError, match="build limit"):
            await previews.create(spec(), client, MetadataRegistry(client))
        assert not previews._building
        with session_scope() as db:
            assert db.scalar(select(func.count()).select_from(Preview)) == 0
