"""Read-only M10 verification against the guarded Paperless 3.1.2 sandbox."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from paperwrench.api.v1.documents import build_query_params
from paperwrench.filters.model import CustomFieldRef
from paperwrench.filters.model import DatasetPageRequest
from paperwrench.filters.model import DatasetQuery
from paperwrench.paperless import MetadataRegistry
from paperwrench.paperless import PaperlessClient
from paperwrench.schemas.model import EqualsRule
from paperwrench.schemas.model import RequiredRule
from paperwrench.schemas.model import SchemaDefinition
from paperwrench.schemas.service import evaluate_document

pytestmark = pytest.mark.live


async def test_vacation_schema_reads_only_and_distinguishes_zero_from_absent(
    live_client: PaperlessClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    methods: list[str] = []
    original = live_client._request

    async def guarded(method: str, path: str, **kwargs: Any) -> httpx.Response:
        methods.append(method)
        assert method == "GET", "M10 evaluation must never write to Paperless"
        return await original(method, path, **kwargs)

    monkeypatch.setattr(live_client, "_request", guarded)
    registry = MetadataRegistry(live_client)
    document_type = await registry.document_type_by_name("Relevé de vacations")
    assert live_client.paperless_version == "3.1.2"
    definitions = {field.id: field for field in await registry.all_custom_fields()}
    period = next(field for field in definitions.values() if field.name == "Période concernée")
    amount = next(field for field in definitions.values() if field.name == "Montant")
    query = DatasetQuery.model_validate({
        "filters": {"root": {"children": [{
            "kind": "condition", "field": {"source": "core", "name": "document_type"},
            "operator": "equals", "value": document_type.id,
        }]}}
    })
    schema = SchemaDefinition(
        name="Relevé de vacations",
        applies_when=query,
        rules=[
            RequiredRule(field=CustomFieldRef(field_id=period.id), field_type="text"),
            RequiredRule(field=CustomFieldRef(field_id=amount.id), field_type="monetary"),
            EqualsRule(
                field=CustomFieldRef(field_id=amount.id), field_type="monetary", value="0.00"
            ),
        ],
    )
    params = await build_query_params(
        DatasetPageRequest(**query.model_dump(exclude_none=True), page_size=25), registry=registry
    )
    seen_zero = False
    seen_absent = False
    page = 1
    while True:
        batch = await live_client.list_documents(params=params, page=page, page_size=25)
        for document in batch.results:
            result = evaluate_document(schema, document, definitions)
            amount_value = document.typed_custom_fields(definitions)[amount.id]
            if amount_value.monetary and amount_value.monetary.amount == 0:
                seen_zero = True
                assert result.rules[1].status == "pass"
                assert result.rules[2].status == "pass"
            if amount_value.kind == "absent":
                seen_absent = True
                assert result.rules[1].status == "fail"
                assert result.rules[2].status == "fail"
        if page * 25 >= batch.count:
            break
        page += 1
    assert seen_zero and seen_absent
    assert methods and set(methods) == {"GET"}
