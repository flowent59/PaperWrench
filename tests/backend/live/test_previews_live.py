"""Guarded M7 Golden Dataset verification on real Paperless."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from sqlalchemy.orm import Session

from paperwrench.api.v1.documents import build_query_params
from paperwrench.filters.model import DatasetPageRequest
from paperwrench.paperless import MetadataRegistry
from paperwrench.paperless import PaperlessClient
from paperwrench.previews.service import PreviewService
from paperwrench.transformations import Transformation
from tests.backend.unit.test_transformations import core
from tests.backend.unit.test_transformations import custom

pytestmark = pytest.mark.live


async def test_golden_dataset_count_list_preview_and_unchanged_documents(
    live_client: PaperlessClient,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
    live_paperless_version: str,
) -> None:
    """No seed/write helpers run inside this probe; inspect every HTTP method."""
    methods: list[str] = []
    request = live_client._request

    async def guarded(method: str, path: str, **kwargs: Any) -> httpx.Response:
        methods.append(method)
        assert method == "GET", "M7 must never write to Paperless"
        return await request(method, path, **kwargs)

    monkeypatch.setattr(live_client, "_request", guarded)
    registry = MetadataRegistry(live_client)
    document_type = await registry.document_type_by_name("Relevé de vacations")
    definitions = {field.id: field for field in await registry.all_custom_fields()}
    period = next(field for field in definitions.values() if field.name == "Période concernée")
    amount = next(field for field in definitions.values() if field.name == "Montant")
    query = DatasetPageRequest.model_validate(
        {
            "ordering": "title",
            "filters": {
                "root": {
                    "children": [
                        {
                            "kind": "condition",
                            "field": core("document_type"),
                            "operator": "equals",
                            "value": document_type.id,
                        }
                    ]
                }
            },
        }
    )
    params = await build_query_params(query, registry=registry)
    count = await live_client.count_documents(params=params)
    before = {
        document.id: document
        async for document in live_client.iter_documents(
            params=params,
            page_size=2,
        )
    }
    assert live_client.paperless_version == live_paperless_version
    assert count == len(before) >= 17
    assert all(document.document_type == document_type.id for document in before.values())
    transformation = Transformation.model_validate(
        {
            "targets": {"source": "dataset", "query": query.dataset().model_dump(mode="json")},
            "operations": [
                {
                    "operation": "template",
                    "field": core("title"),
                    "template": "Relevé de vacations \u2013 {Période concernée}",
                    "bindings": {"Période concernée": custom(period.id)},
                }
            ],
        }
    )
    previews = PreviewService()
    preview = await previews.create(transformation, live_client, registry)
    assert preview.matched == preview.evaluated == count
    assert preview.errors > 0 and preview.changed > 0
    items = previews.page(preview.id, 1, 100).items
    assert {row.document_id for row in items} == set(before)
    for row in items:
        value = before[row.document_id].custom_field_map.get(period.id)
        change = row.changes[0]
        if value is None or value == "":
            assert change.issue is not None and change.issue.code == "TEMPLATE_UNRESOLVED"
        else:
            assert change.intended is not None
            assert change.intended.raw == f"Relevé de vacations \u2013 {value}"

    zero_ids = [
        document.id
        for document in before.values()
        if document.custom_field_map.get(amount.id) == "EUR0.00"
    ]
    assert zero_ids
    zero_spec = Transformation.model_validate(
        {
            "targets": {"source": "ids", "document_ids": zero_ids},
            "operations": [
                {
                    "operation": "template",
                    "field": core("title"),
                    "template": "{amount}",
                    "bindings": {"amount": custom(amount.id)},
                }
            ],
        }
    )
    zero = await previews.create(zero_spec, live_client, registry)
    for row in previews.page(zero.id, 1, 25).items:
        assert row.changes[0].intended is not None
        assert row.changes[0].intended.raw == "EUR0.00"
    after = {
        document.id: document
        async for document in live_client.iter_documents(
            params=params,
            page_size=2,
        )
    }
    assert before == after
    for document_id, original in before.items():
        assert await live_client.get_document(document_id) == original
    assert methods and set(methods) == {"GET"}
