"""M10 rule semantics, storage contract and safe dataset evaluation."""

from __future__ import annotations

from typing import Any

import pytest
import respx
from fastapi.testclient import TestClient
from httpx import Request
from httpx import Response
from pydantic import ValidationError

from paperwrench.db.engine import get_session_factory
from paperwrench.db.models import DocumentSchema
from paperwrench.errors import PaperWrenchError
from paperwrench.filters.catalog import FieldCatalog
from paperwrench.filters.model import CustomFieldRef
from paperwrench.filters.model import DatasetQuery
from paperwrench.paperless.models import CustomField
from paperwrench.paperless.models import CustomFieldValue
from paperwrench.paperless.models import Document
from paperwrench.schemas.model import EqualsRule
from paperwrench.schemas.model import RequiredRule
from paperwrench.schemas.model import SchemaDefinition
from paperwrench.schemas.model import StoredRules
from paperwrench.schemas.model import StoredScope
from paperwrench.schemas.service import evaluate_document
from paperwrench.schemas.service import validate_definition

BASE = "http://paperless.test"
HEADERS = {"X-Api-Version": "10", "X-Version": "3.1.2"}
PERIOD = CustomField(id=1, name="Période concernée", data_type="string")
AMOUNT = CustomField(id=2, name="Montant", data_type="monetary")
SELECT = CustomField(
    id=3,
    name="Catégorie",
    data_type="select",
    extra_data={"select_options": [{"id": "choice-a", "label": "Approuvé"}]},
)
BOOLEAN = CustomField(id=4, name="Validé", data_type="boolean")
DATE = CustomField(id=5, name="Date", data_type="date")
COUNT = CustomField(id=6, name="Nombre", data_type="integer")
FLOAT = CustomField(id=7, name="Taux", data_type="float")
DEFINITIONS = {field.id: field for field in [PERIOD, AMOUNT, SELECT, BOOLEAN, DATE, COUNT, FLOAT]}


def schema() -> SchemaDefinition:
    return SchemaDefinition(
        name="Relevé de vacations",
        applies_when=DatasetQuery(),
        rules=[
            RequiredRule(field=CustomFieldRef(field_id=1), field_type="text"),
            RequiredRule(field=CustomFieldRef(field_id=2), field_type="monetary"),
        ],
    )


def document(values: list[tuple[int, Any]]) -> Document:
    return Document(
        id=10,
        title="Vacations septembre",
        custom_fields=[CustomFieldValue(field=field, value=value) for field, value in values],
    )


@pytest.mark.parametrize(
    ("period", "amount", "expected"),
    [
        ("septembre", "EUR0.00", ["pass", "pass"]),
        ("septembre", None, ["pass", "fail"]),
        ("", "EUR0.00", ["fail", "pass"]),
        (None, "EUR0.00", ["fail", "pass"]),
    ],
)
def test_vacation_required_states(period: Any, amount: Any, expected: list[str]) -> None:
    values = [(1, period), (2, amount)]
    result = evaluate_document(schema(), document(values), DEFINITIONS)
    assert [rule.status for rule in result.rules] == expected
    assert result.rules[0].value_kind == ("null" if period is None else "present")


def test_absent_amount_fails_and_false_is_present() -> None:
    definition = schema()
    definition.rules.append(RequiredRule(field=CustomFieldRef(field_id=4), field_type="boolean"))
    result = evaluate_document(definition, document([(1, "septembre"), (4, False)]), DEFINITIONS)
    assert [rule.status for rule in result.rules] == ["pass", "fail", "pass"]
    assert result.rules[1].value_kind == "absent"


def test_equals_decimal_and_select_id_not_label() -> None:
    definition = schema()
    definition.rules = [
        EqualsRule(field=CustomFieldRef(field_id=2), field_type="monetary", value="0.00"),
        EqualsRule(field=CustomFieldRef(field_id=3), field_type="select", value="choice-a"),
    ]
    validate_definition(definition, FieldCatalog(list(DEFINITIONS.values())))
    result = evaluate_document(
        definition, document([(2, "EUR0.00"), (3, "choice-a")]), DEFINITIONS
    )
    assert result.status == "pass"
    assert result.rules[0].actual == 0  # Decimal compares numerically without float conversion
    assert result.rules[0].model_dump(mode="json")["actual"] == "0.00"
    assert result.rules[1].actual == "choice-a"
    definition.rules[1] = EqualsRule(
        field=CustomFieldRef(field_id=3), field_type="select", value="Approuvé"
    )
    with pytest.raises(PaperWrenchError):
        validate_definition(definition, FieldCatalog(list(DEFINITIONS.values())))
    definition.rules[0] = EqualsRule(
        field=CustomFieldRef(field_id=2), field_type="monetary", value=0.0
    )
    with pytest.raises(PaperWrenchError):
        validate_definition(definition, FieldCatalog(list(DEFINITIONS.values())))


def test_equals_uses_boolean_date_integer_and_float_types() -> None:
    definition = schema()
    definition.rules = [
        EqualsRule(field=CustomFieldRef(field_id=4), field_type="boolean", value=False),
        EqualsRule(field=CustomFieldRef(field_id=5), field_type="date", value="2026-09-25"),
        EqualsRule(field=CustomFieldRef(field_id=6), field_type="integer", value=0),
        EqualsRule(field=CustomFieldRef(field_id=7), field_type="float", value=0),
    ]
    validate_definition(definition, FieldCatalog(list(DEFINITIONS.values())))
    result = evaluate_document(
        definition,
        document([(4, False), (5, "2026-09-25"), (6, 0), (7, 0.0)]),
        DEFINITIONS,
    )
    assert result.status == "pass"
    definition.rules[0] = EqualsRule(
        field=CustomFieldRef(field_id=4), field_type="boolean", value=0
    )
    with pytest.raises(PaperWrenchError):
        validate_definition(definition, FieldCatalog(list(DEFINITIONS.values())))


def test_versioned_roundtrip_and_malformed_rules() -> None:
    original = schema()
    scope = StoredScope(query=original.applies_when)
    rules = StoredRules(items=original.rules)
    assert StoredScope.model_validate_json(scope.model_dump_json()) == scope
    assert StoredRules.model_validate_json(rules.model_dump_json()) == rules
    with pytest.raises(ValidationError):
        StoredRules.model_validate({"version": 1, "items": [{"kind": "regex"}]})
    with pytest.raises(ValidationError):
        StoredRules.model_validate({"version": 2, "items": []})


def test_deleted_or_retyped_field_refuses_evaluation() -> None:
    definition = schema()
    with pytest.raises(PaperWrenchError, match="no longer exists"):
        validate_definition(definition, FieldCatalog([AMOUNT]))
    changed = CustomField(id=1, name="Période concernée", data_type="date")
    with pytest.raises(PaperWrenchError, match="changed type"):
        validate_definition(definition, FieldCatalog([changed, AMOUNT]))


def _page(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {"count": 3, "next": None, "previous": None, "results": items}


@respx.mock
def test_crud_and_paginated_read_only_evaluation(client: TestClient) -> None:
    metadata = respx.get(f"{BASE}/api/custom_fields/").mock(
        return_value=Response(200, json=_page([
            {"id": 1, "name": "Période concernée", "data_type": "string"},
            {"id": 2, "name": "Montant", "data_type": "monetary"},
        ]), headers=HEADERS)
    )
    def page_reply(request: Request) -> Response:
        page = request.url.params.get("page")
        items = (
            [document([(1, "septembre")]).model_dump(mode="json")]
            if page == "2"
            else [document([(1, "septembre"), (2, "EUR0.00")]).model_dump(mode="json")]
        )
        return Response(
            200,
            json={"count": 30, "next": None, "previous": None, "results": items},
            headers=HEADERS,
        )

    documents = respx.get(f"{BASE}/api/documents/").mock(side_effect=page_reply)
    body = schema().model_dump(mode="json")
    created = client.post("/api/v1/schemas", json=body)
    assert created.status_code == 201, created.text
    schema_id = created.json()["id"]
    assert client.get("/api/v1/schemas").json()[0]["name"] == body["name"]
    assert client.get(f"/api/v1/schemas/{schema_id}").status_code == 200
    body["description"] = "Updated"
    assert client.put(f"/api/v1/schemas/{schema_id}", json=body).json()["description"] == "Updated"
    evaluated = client.get(f"/api/v1/schemas/{schema_id}/evaluate?page=1&page_size=25")
    assert evaluated.status_code == 200, evaluated.text
    assert [item["status"] for item in evaluated.json()["items"]] == ["pass"]
    assert evaluated.json()["page_count"] == 2
    second = client.get(f"/api/v1/schemas/{schema_id}/evaluate?page=2&page_size=25")
    assert [item["status"] for item in second.json()["items"]] == ["fail"]
    assert documents.call_count == 2
    assert documents.calls[-1].request.url.params["page"] == "2"
    oversized = client.get(f"/api/v1/schemas/{schema_id}/evaluate?page=1&page_size=250")
    assert oversized.status_code == 422
    assert documents.call_count == 2
    assert metadata.called
    assert client.delete(f"/api/v1/schemas/{schema_id}").status_code == 204
    assert client.get(f"/api/v1/schemas/{schema_id}").status_code == 404


@respx.mock
def test_invalid_scope_and_rule_never_list_documents(client: TestClient) -> None:
    respx.get(f"{BASE}/api/custom_fields/").mock(
        return_value=Response(200, json=_page([
            {"id": 1, "name": "Période concernée", "data_type": "string"},
            {"id": 2, "name": "Montant", "data_type": "monetary"},
        ]), headers=HEADERS)
    )
    documents = respx.get(f"{BASE}/api/documents/").mock(
        return_value=Response(200, json=_page([]), headers=HEADERS)
    )
    body = schema().model_dump(mode="json")
    body["applies_when"]["filters"] = {
        "root": {"kind": "group", "operator": "or", "children": [
            {"kind": "condition", "field": {"source": "core", "name": "title"},
             "operator": "equals", "value": "A"},
            {"kind": "condition", "field": {"source": "core", "name": "title"},
             "operator": "equals", "value": "B"},
        ]}
    }
    response = client.post("/api/v1/schemas", json=body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "FILTER_NOT_COMPILABLE"
    assert documents.call_count == 0
    body["applies_when"] = {}
    body["rules"][0]["field"]["field_id"] = 999
    assert client.post("/api/v1/schemas", json=body).status_code == 422
    assert documents.call_count == 0


@respx.mock
def test_saved_scope_corruption_is_refused_before_any_document_request(client: TestClient) -> None:
    respx.get(f"{BASE}/api/custom_fields/").mock(
        return_value=Response(200, json=_page([
            {"id": 1, "name": "Période concernée", "data_type": "string"},
            {"id": 2, "name": "Montant", "data_type": "monetary"},
        ]), headers=HEADERS)
    )
    documents = respx.get(f"{BASE}/api/documents/").mock(
        return_value=Response(200, json=_page([]), headers=HEADERS)
    )
    created = client.post("/api/v1/schemas", json=schema().model_dump(mode="json"))
    assert created.status_code == 201
    schema_id = created.json()["id"]
    with get_session_factory()() as db:
        row = db.get(DocumentSchema, schema_id)
        assert row is not None
        row.applies_when_json = '{"version":2,"query":{}}'
        db.commit()
    response = client.get(f"/api/v1/schemas/{schema_id}/evaluate")
    assert response.status_code == 422
    assert documents.call_count == 0
