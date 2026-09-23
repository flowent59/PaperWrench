"""Enforce #7 before any Inspector write route exists; no upstream claims."""

import asyncio
import json
from typing import Any

import httpx
import pytest

from paperwrench.config import Settings
from paperwrench.paperless.client import PaperlessClient
from paperwrench.paperless.errors import PaperlessConflictError
from paperwrench.paperless.errors import PaperlessValidationError
from paperwrench.paperless.models import Document
from paperwrench.paperless.mutations import DocumentMutationCoordinator
from paperwrench.paperless.mutations import revision


@pytest.mark.parametrize(
    "key",
    [
        "custom_fields",
        "customFields",
        "custom_fields[]",
        "custom_fields.0",
        "custom_fields[0]",
        " custom_fields",
        "CUSTOM_FIELDS",
        "custom_fields\u0000",
        "owner",
        "set_permissions",
    ],
)
@pytest.mark.parametrize("value", [None, [], [{"field": 1, "value": "lost"}]])
async def test_core_boundary_rejects_aliases_without_io(
    settings: Settings,
    key: str,
    value: Any,
) -> None:
    def unexpected(request: httpx.Request) -> httpx.Response:
        pytest.fail(f"Unexpected {request.method}")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(unexpected), base_url=settings.paperless_url
    ) as http:
        client = PaperlessClient(settings, http_client=http)
        with pytest.raises(PaperlessValidationError):
            await client.update_document(1, {"title": "valid", key: value})


async def test_coordinated_writers_conflict_and_preserve_all_values(settings: Settings) -> None:
    state: dict[str, Any] = {
        "id": 1,
        "title": "before",
        "user_can_change": True,
        "custom_fields": [
            {"field": 1, "value": "old"},
            {"field": 2, "value": "EUR0.00"},
            {"field": 3, "value": False},
            {"field": 4, "value": None},
            {"field": 5, "value": ""},
            {"field": 6, "value": 0},
        ],
    }
    original = Document.model_validate(state)
    entered = asyncio.Event()
    release = asyncio.Event()
    reads = 0
    patches: list[dict[str, Any]] = []

    async def transport(request: httpx.Request) -> httpx.Response:
        nonlocal reads
        if request.method == "GET":
            reads += 1
            if reads == 1:
                entered.set()
                await release.wait()
        else:
            payload = json.loads(request.content)
            patches.append(payload)
            state.update(payload)
        return httpx.Response(200, json=state)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport), base_url=settings.paperless_url
    ) as http:
        coordinator = DocumentMutationCoordinator()
        a = PaperlessClient(settings, http_client=http, coordinator=coordinator)
        b = PaperlessClient(settings, http_client=http, coordinator=coordinator)

        async def write(client: PaperlessClient, value: str) -> None:
            await client.mutate_document(
                1,
                expected_revision=revision(original),
                core={"title": value},
                custom_updates=[{"field": 1, "value": value}],
                remove_custom_fields=[],
                acknowledge_external_race=True,
            )

        first = asyncio.create_task(write(a, "A"))
        await entered.wait()
        second = asyncio.create_task(write(b, "B"))
        await asyncio.sleep(0)
        assert reads == 1
        release.set()
        await first
        with pytest.raises(PaperlessConflictError):
            await second
        assert len(patches) == 1
        assert patches[0]["title"] == "A"
        assert state["custom_fields"][1:] == [v.model_dump() for v in original.custom_fields[1:]]
        assert not coordinator._entries


async def test_custom_write_requires_explicit_race_ack(settings: Settings) -> None:
    async with PaperlessClient(settings) as client:
        with pytest.raises(PaperlessValidationError):
            await client.update_custom_fields(1, [{"field": 1, "value": "x"}])


async def test_lock_cancellation_and_other_documents() -> None:
    coordinator = DocumentMutationCoordinator()
    async with coordinator.hold(1):

        async def waiter() -> None:
            async with coordinator.hold(1):
                pytest.fail("Cancelled waiter acquired the lock")

        pending = asyncio.create_task(waiter())
        await asyncio.sleep(0)
        async with coordinator.hold(2):
            pass
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
    assert not coordinator._entries
