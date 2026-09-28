"""M12 static membership against the disposable Paperless sandbox."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from sqlalchemy.orm import Session

from paperwrench.api.v1.collections import CollectionCreate
from paperwrench.api.v1.collections import create_collection
from paperwrench.api.v1.collections import list_members
from paperwrench.db.models import CollectionDocument
from paperwrench.paperless import MetadataRegistry
from paperwrench.paperless import PaperlessClient

pytestmark = pytest.mark.live


async def test_static_vacation_ids_survive_reload_and_missing_member(
    live_client: PaperlessClient, session: Session, monkeypatch: pytest.MonkeyPatch,
    live_paperless_version: str,
) -> None:
    methods: list[str] = []
    original = live_client._request

    async def guarded(method: str, path: str, **kwargs: Any) -> httpx.Response:
        methods.append(method)
        assert method == "GET", "Collections must never mutate Paperless"
        return await original(method, path, **kwargs)

    monkeypatch.setattr(live_client, "_request", guarded)
    registry = MetadataRegistry(live_client)
    document_type = await registry.document_type_by_name("Relevé de vacations")
    assert live_client.paperless_version == live_paperless_version
    first = await live_client.list_documents(
        params={"document_type__id": document_type.id}, page=1, page_size=2
    )
    second = await live_client.list_documents(
        params={"document_type__id": document_type.id}, page=2, page_size=2
    )
    ids = [first.results[0].id, second.results[0].id]
    assert ids[0] != ids[1]
    owner_id = int((await live_client.get_profile())["id"])
    created = await create_collection(
        CollectionCreate(name="Live vacations", document_ids=ids),
        session,
        live_client,
        owner_id,
    )
    assert created.member_count == 2
    page = await list_members(created.id, 1, 25, session, live_client, registry, owner_id)
    assert {item.document_id for item in page.items} == set(ids)
    assert all(item.available for item in page.items)
    session.add(CollectionDocument(collection_id=created.id, document_id=999999999))
    session.commit()
    reloaded = await list_members(
        created.id, 1, 25, session, live_client, registry, owner_id
    )
    missing = next(item for item in reloaded.items if item.document_id == 999999999)
    assert not missing.available and missing.document is None
    assert set(methods) == {"GET"}
