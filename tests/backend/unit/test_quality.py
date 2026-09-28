"""M11 counts, exact drill-downs and inaccessible ID handling."""

from __future__ import annotations

from typing import Any

import respx
from fastapi.testclient import TestClient
from httpx import Response

from paperwrench.db.engine import get_session_factory
from paperwrench.db.models import DocumentSchema
from paperwrench.filters.catalog import FieldCatalog
from paperwrench.filters.model import CoreFieldRef
from paperwrench.filters.model import CustomFieldRef
from paperwrench.filters.model import DatasetQuery
from paperwrench.filters.model import FilterCondition
from paperwrench.filters.model import FilterGroup
from paperwrench.filters.model import FilterOperator
from paperwrench.filters.model import FilterSet
from paperwrench.filters.service import validate_and_compile
from paperwrench.paperless.models import CustomField
from paperwrench.paperless.models import CustomFieldValue
from paperwrench.paperless.models import Document
from paperwrench.quality.service import exact_rule_query
from paperwrench.schemas.model import EqualsRule
from paperwrench.schemas.model import RequiredRule
from paperwrench.schemas.model import SchemaDefinition
from paperwrench.schemas.model import StoredRules
from paperwrench.schemas.model import StoredScope

BASE = "http://paperless.test"
HEADERS = {"X-Api-Version": "10", "X-Version": "3.1.2"}
AMOUNT = CustomField(id=2, name="Montant", data_type="monetary")


def definition() -> SchemaDefinition:
    return SchemaDefinition(
        name="Vacations",
        applies_when=DatasetQuery(),
        rules=[RequiredRule(field=CustomFieldRef(field_id=2), field_type="monetary")],
    )


def doc(document_id: int, value: str | None = None) -> dict[str, Any]:
    fields = [] if value is None else [CustomFieldValue(field=2, value=value)]
    return Document(
        id=document_id, title=f"Vacation {document_id}", custom_fields=fields
    ).model_dump(mode="json")


def test_exact_required_complement_and_unsupported_rules() -> None:
    schema = definition()
    catalog = FieldCatalog([AMOUNT])
    query = exact_rule_query(schema, schema.rules[0], catalog)
    assert query is not None and query.filters is not None
    group = query.filters.root.children[0]
    assert isinstance(group, FilterGroup)
    assert all(isinstance(child, FilterCondition) for child in group.children)
    assert [child.operator for child in group.children if isinstance(child, FilterCondition)] == [
        "is_missing",
        "is_null",
    ]
    assert (
        exact_rule_query(
            schema,
            EqualsRule(field=CustomFieldRef(field_id=2), field_type="monetary", value="0.00"),
            catalog,
        )
        is None
    )
    assert (
        exact_rule_query(
            schema, RequiredRule(field=CoreFieldRef(name="title"), field_type="text"), catalog
        )
        is None
    )
    assert (
        exact_rule_query(
            schema,
            RequiredRule(field=CustomFieldRef(field_id=3), field_type="document_link"),
            catalog,
        )
        is None
    )


def test_compiler_refusal_uses_ids_instead_of_approximate_filter() -> None:
    schema = definition()
    catalog = FieldCatalog([AMOUNT])
    schema.applies_when.filters = FilterSet(
        root=FilterGroup(children=[
            FilterCondition(
                field=CustomFieldRef(field_id=2), operator=FilterOperator.EQUALS, value="1.00"
            )
            for _ in range(19)
        ])
    )
    validate_and_compile(schema.applies_when.filters, catalog)
    assert exact_rule_query(schema, schema.rules[0], catalog) is None


@respx.mock
def test_quality_page_counts_are_bounded_and_zero_is_not_missing(client: TestClient) -> None:
    respx.get(f"{BASE}/api/custom_fields/").mock(
        return_value=Response(
            200,
            json={
                "count": 1,
                "next": None,
                "previous": None,
                "results": [{"id": 2, "name": "Montant", "data_type": "monetary"}],
            },
            headers=HEADERS,
        )
    )

    def documents(request: Any) -> Response:
        items = [doc(3)] if request.url.params.get("page") == "2" else [doc(1), doc(2, "EUR0.00")]
        return Response(
            200,
            json={"count": 27, "next": None, "previous": None, "results": items},
            headers=HEADERS,
        )

    listing = respx.get(f"{BASE}/api/documents/").mock(side_effect=documents)
    created = client.post("/api/v1/schemas", json=definition().model_dump(mode="json"))
    assert created.status_code == 201, created.text
    schema_id = created.json()["id"]
    first = client.get(f"/api/v1/quality/schemas/{schema_id}?page=1&page_size=25")
    assert first.status_code == 200, first.text
    body = first.json()
    assert (body["evaluated_count"], body["violation_count"], body["dataset_total"]) == (2, 1, 27)
    assert body["page_count"] == 2
    assert [item["document_id"] for item in body["items"]] == [1]
    assert body["items"][0]["rule"]["value_kind"] == "absent"
    assert body["rules"][0]["exact_query"] is not None
    second = client.get(f"/api/v1/quality/schemas/{schema_id}?page=2&page_size=25")
    assert second.json()["evaluated_count"] == 1
    assert second.json()["violation_count"] == 1
    assert listing.call_count == 2
    assert all(call.request.method == "GET" for call in listing.calls)
    assert client.get(f"/api/v1/quality/schemas/{schema_id}?page_size=1").status_code == 422
    assert listing.call_count == 2


@respx.mock
def test_removed_metadata_blocks_evaluation_before_document_list(client: TestClient) -> None:
    respx.get(f"{BASE}/api/custom_fields/").mock(
        return_value=Response(
            200,
            json={
                "count": 0,
                "next": None,
                "previous": None,
                "results": [],
            },
            headers=HEADERS,
        )
    )
    listing = respx.get(f"{BASE}/api/documents/").mock(
        return_value=Response(
            200,
            json={
                "count": 0,
                "next": None,
                "previous": None,
                "results": [],
            },
            headers=HEADERS,
        )
    )
    schema = definition()
    with get_session_factory()() as db:
        row = DocumentSchema(
            owner_id=1,
            name=schema.name,
            description=None,
            applies_when_json=StoredScope(query=schema.applies_when).model_dump_json(),
            rules_json=StoredRules(items=schema.rules).model_dump_json(),
        )
        db.add(row)
        db.commit()
        schema_id = row.id
    response = client.get(f"/api/v1/quality/schemas/{schema_id}")
    assert response.status_code == 422
    assert "no longer exists" in response.text
    assert listing.call_count == 0


@respx.mock
def test_explicit_ids_skip_deleted_and_forbidden_without_leaking_details(
    client: TestClient,
) -> None:
    respx.get(f"{BASE}/api/custom_fields/").mock(
        return_value=Response(
            200,
            json={
                "count": 0,
                "next": None,
                "previous": None,
                "results": [],
            },
            headers=HEADERS,
        )
    )
    respx.get(f"{BASE}/api/documents/1/").mock(
        return_value=Response(200, json=doc(1), headers=HEADERS)
    )
    respx.get(f"{BASE}/api/documents/2/").mock(
        return_value=Response(404, json={"detail": "private"})
    )
    respx.get(f"{BASE}/api/documents/3/").mock(
        return_value=Response(403, json={"detail": "private"})
    )
    response = client.post("/api/v1/documents/by-ids", json={"document_ids": [1, 2, 3]})
    assert response.status_code == 200, response.text
    assert [item["id"] for item in response.json()["items"]] == [1]
    assert response.json()["unavailable_count"] == 2
    assert "private" not in response.text
