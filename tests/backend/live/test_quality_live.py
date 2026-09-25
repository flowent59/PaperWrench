"""M11 exact Quality drill-down against the guarded Golden Dataset."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from paperwrench.api.v1.documents import build_query_params
from paperwrench.filters.catalog import FieldCatalog
from paperwrench.filters.model import CustomFieldRef
from paperwrench.filters.model import DatasetPageRequest
from paperwrench.filters.model import DatasetQuery
from paperwrench.paperless import MetadataRegistry
from paperwrench.paperless import PaperlessClient
from paperwrench.quality.service import exact_rule_query
from paperwrench.schemas.model import RequiredRule
from paperwrench.schemas.model import SchemaDefinition
from paperwrench.schemas.service import evaluate_document

pytestmark = pytest.mark.live


async def test_quality_missing_amount_drilldown_excludes_zero(
    live_client: PaperlessClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    methods: list[str] = []
    original = live_client._request

    async def guarded(method: str, path: str, **kwargs: Any) -> httpx.Response:
        methods.append(method)
        assert method == "GET", "Quality must never write to Paperless"
        return await original(method, path, **kwargs)

    monkeypatch.setattr(live_client, "_request", guarded)
    registry = MetadataRegistry(live_client)
    document_type = await registry.document_type_by_name("Relevé de vacations")
    assert live_client.paperless_version == "3.1.2"
    definitions = {field.id: field for field in await registry.all_custom_fields()}
    amount = next(field for field in definitions.values() if field.name == "Montant")
    scope = DatasetQuery.model_validate(
        {
            "filters": {
                "root": {
                    "children": [
                        {
                            "kind": "condition",
                            "field": {"source": "core", "name": "document_type"},
                            "operator": "equals",
                            "value": document_type.id,
                        }
                    ]
                }
            }
        }
    )
    rule = RequiredRule(field=CustomFieldRef(field_id=amount.id), field_type="monetary")
    schema = SchemaDefinition(name="Vacations", applies_when=scope, rules=[rule])
    exact = exact_rule_query(schema, rule, FieldCatalog(list(definitions.values())))
    assert exact is not None

    async def all_ids(query: DatasetQuery) -> tuple[set[int], int]:
        params = await build_query_params(
            DatasetPageRequest(**query.model_dump(exclude_none=True), page_size=25),
            registry=registry,
        )
        seen: set[int] = set()
        page = 1
        total = 0
        while True:
            batch = await live_client.list_documents(params=params, page=page, page_size=25)
            total = batch.count
            seen.update(document.id for document in batch.results)
            if page * 25 >= total:
                break
            page += 1
        return seen, total

    scope_params = await build_query_params(
        DatasetPageRequest(**scope.model_dump(exclude_none=True), page_size=25), registry=registry
    )
    failures: set[int] = set()
    zero_ids: set[int] = set()
    page = 1
    while True:
        batch = await live_client.list_documents(params=scope_params, page=page, page_size=25)
        for document in batch.results:
            if evaluate_document(schema, document, definitions).rules[0].status == "fail":
                failures.add(document.id)
            typed = document.typed_custom_fields(definitions)[amount.id]
            if typed.monetary and typed.monetary.amount == 0:
                zero_ids.add(document.id)
        if page * 25 >= batch.count:
            break
        page += 1
    drilldown_ids, drilldown_total = await all_ids(exact)
    assert failures and zero_ids
    assert drilldown_ids == failures
    assert drilldown_total == len(failures)
    assert not (drilldown_ids & zero_ids)
    assert methods and set(methods) == {"GET"}
