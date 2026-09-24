"""M6's pure operation matrix and read-only HTTP boundary."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from pydantic import ValidationError

from paperwrench.paperless.models import CustomField
from paperwrench.paperless.models import CustomFieldValue
from paperwrench.paperless.models import Document
from paperwrench.transformations import Transformation
from paperwrench.transformations import evaluate
from paperwrench.transformations import validate
from tests.backend.unit.test_api_documents import _document
from tests.backend.unit.test_api_documents import _mock_reference_endpoints


def core(name: str) -> dict[str, Any]:
    return {"source": "core", "name": name}


def custom(field_id: int) -> dict[str, Any]:
    return {"source": "custom_field", "field_id": field_id}


def spec(*operations: dict[str, Any]) -> Transformation:
    return Transformation.model_validate({
        "targets": {"source": "ids", "document_ids": [100]},
        "operations": list(operations),
    })


@pytest.fixture
def definitions() -> dict[int, CustomField]:
    fields = [
        CustomField(id=1, name="Période concernée", data_type="string"),
        CustomField(id=2, name="Montant", data_type="monetary"),
        CustomField(id=3, name="Validé", data_type="boolean"),
        CustomField(id=4, name="Date", data_type="date"),
        CustomField(id=5, name="Option", data_type="select", extra_data={
            "select_options": [{"id": "option-id", "label": "Été"}],
        }),
        CustomField(id=6, name="Count", data_type="integer"),
        CustomField(id=7, name="Unsupported", data_type="float"),
        CustomField(id=8, name="Empty", data_type="string"),
        CustomField(id=9, name="Null", data_type="string"),
    ]
    return {field.id: field for field in fields}


@pytest.fixture
def document() -> Document:
    return Document(id=100, title="Ancien titre", created="2026-01-02", custom_fields=[
        {"field": 1, "value": "Juillet 2026"},
        {"field": 2, "value": "EUR0.00"},
        {"field": 3, "value": False},
        {"field": 4, "value": "2026-07-01"},
        {"field": 5, "value": "option-id"},
        {"field": 6, "value": 0},
        {"field": 8, "value": ""},
        {"field": 9, "value": None},
    ])


def one(document: Document, definitions: dict[int, CustomField], operation: dict[str, Any]) -> Any:
    return evaluate(document, spec(operation), definitions).changes[0]


def test_set_valid_invalid_and_unchanged(
    document: Document, definitions: dict[int, CustomField]
) -> None:
    changed = one(
        document, definitions, {"operation": "set", "field": custom(2), "value": "EUR1.25"}
    )
    assert (changed.status, changed.before.raw, changed.intended.raw) == (
        "change", "EUR0.00", "EUR1.25"
    )
    assert str(changed.before.monetary) == "EUR0.00"
    assert str(changed.intended.monetary) == "EUR1.25"
    assert one(document, definitions, {
        "operation": "set", "field": custom(2), "value": "EUR0.00"
    }).status == "unchanged"
    for field, value in [(custom(2), 1.25), (custom(3), 0), (custom(5), "Été"),
                         (core("title"), None)]:
        result = one(document, definitions, {"operation": "set", "field": field, "value": value})
        assert result.status == "error"
        assert result.issue.code == "INVALID_VALUE"
    selected = one(document, definitions, {
        "operation": "set", "field": custom(5), "value": "option-id"
    })
    assert selected.status == "unchanged"
    assert selected.intended.select_option_id == "option-id"
    assert selected.intended.select_label == "Été"


def test_clear_distinguishes_absent_null_and_core(
    document: Document, definitions: dict[int, CustomField]
) -> None:
    absent = one(document, definitions, {
        "operation": "clear", "field": custom(8), "state": "absent"
    })
    null = one(document, definitions, {
        "operation": "clear", "field": custom(8), "state": "null"
    })
    assert (absent.before.kind, absent.intended.kind) == ("present", "absent")
    assert (null.before.kind, null.intended.kind) == ("present", "null")
    assert one(document, definitions, {
        "operation": "clear", "field": custom(9), "state": "null"
    }).status == "unchanged"
    assert one(document, definitions, {
        "operation": "clear", "field": core("title")
    }).issue.code == "INVALID_CLEAR"
    assert one(document, definitions, {
        "operation": "clear", "field": custom(1)
    }).issue.code == "INVALID_CLEAR"


def test_replace_literal_and_no_match(
    document: Document, definitions: dict[int, CustomField]
) -> None:
    result = one(document, definitions, {
        "operation": "replace", "field": core("title"), "find": "titre", "replacement": "été"
    })
    assert result.intended.raw == "Ancien été"
    assert one(document, definitions, {
        "operation": "replace", "field": core("title"), "find": "xyz", "replacement": "a"
    }).status == "unchanged"
    assert one(document, definitions, {
        "operation": "replace", "field": custom(9), "find": "x", "replacement": "y"
    }).issue.code == "INVALID_OPERATION"
    assert one(document, definitions, {
        "operation": "replace", "field": custom(2), "find": "0", "replacement": "1"
    }).issue.code == "INVALID_OPERATION"


def test_template_core_custom_falsy_money_select_date_and_unicode(
    document: Document, definitions: dict[int, CustomField]
) -> None:
    bindings = {
        "Période concernée": custom(1), "old": core("title"), "amount": custom(2),
        "valid": custom(3), "count": custom(6), "option": custom(5),
        "date": custom(4), "created": core("created"),
    }
    result = one(document, definitions, {
        "operation": "template", "field": custom(1),
        "template": (
            "{Période concernée} | {old} | {amount} | {valid} | "
            "{count} | {option} | {date} | {created}"
        ),
        "bindings": bindings,
    })
    assert result.intended.raw == (
        "Juillet 2026 | Ancien titre | EUR0.00 | false | 0 | Été | 2026-07-01 | 2026-01-02"
    )
    vacation = one(document, definitions, {
        "operation": "template", "field": core("title"),
        "template": "Relevé de vacations \u2013 {Période concernée}",
        "bindings": {"Période concernée": custom(1)},
    })
    assert vacation.intended.raw == "Relevé de vacations \u2013 Juillet 2026"


@pytest.mark.parametrize("field_id", [8, 9, 42])
def test_template_unresolved_states(
    document: Document, definitions: dict[int, CustomField], field_id: int
) -> None:
    result = one(document, definitions, {
        "operation": "template", "field": core("title"),
        "template": "x {value}", "bindings": {"value": custom(field_id)},
    })
    assert result.status == "error"
    assert result.issue.code == ("UNKNOWN_FIELD" if field_id == 42 else "TEMPLATE_UNRESOLVED")


def test_malformed_template_unknown_ref_and_unsupported_type(
    document: Document, definitions: dict[int, CustomField]
) -> None:
    for template, bindings in [("{missing}", {}), ("{x} {y}", {"x": custom(1)}),
                               ("{x", {"x": custom(1)}), ("{x.__class__}", {})]:
        result = one(document, definitions, {
            "operation": "template", "field": core("title"),
            "template": template, "bindings": bindings,
        })
        assert result.issue.code == "INVALID_TEMPLATE"
    assert one(document, definitions, {
        "operation": "set", "field": custom(7), "value": 1.0
    }).issue.code == "UNSUPPORTED_FIELD_TYPE"
    assert one(document, definitions, {
        "operation": "set", "field": custom(42), "value": "x"
    }).issue.code == "UNKNOWN_FIELD"
    document.custom_fields.append(CustomFieldValue(field=42, value="orphaned"))
    orphaned = one(document, definitions, {
        "operation": "set", "field": custom(42), "value": "x"
    })
    assert (orphaned.before.kind, orphaned.before.raw) == ("present", "orphaned")


def test_multiple_changes_are_independent_and_deterministic(
    document: Document, definitions: dict[int, CustomField]
) -> None:
    transformation = spec(
        {"operation": "set", "field": custom(1), "value": "New"},
        {"operation": "template", "field": core("title"), "template": "{period}",
         "bindings": {"period": custom(1)}},
    )
    first = evaluate(document, transformation, definitions)
    reordered = dict(reversed(list(definitions.items())))
    assert first == evaluate(document, transformation, reordered)
    assert first == evaluate(document, transformation, definitions)
    assert all(change.intended is not None for change in first.changes)
    assert [change.intended.raw for change in first.changes if change.intended] == [
        "New", "Juillet 2026"
    ]
    assert document.custom_fields[0].value == "Juillet 2026"


def test_closed_spec_and_target_sources() -> None:
    for payload in [
        {"targets": {"source": "ids", "document_ids": [1, 1]}, "operations": []},
        {"targets": {"source": "ids", "document_ids": [1]}, "operations": [
            {"operation": "delete", "field": core("title")}]},
        {"targets": {"source": "ids", "document_ids": [1]}, "operations": [
            {"operation": "set", "field": core("title"), "value": "x", "unknown": True}]},
    ]:
        with pytest.raises(ValidationError):
            Transformation.model_validate(payload)
    dataset = Transformation.model_validate({
        "targets": {"source": "dataset", "query": {"search": {"mode": "title", "text": "été"}}},
        "operations": [{"operation": "clear", "field": custom(1), "state": "absent"}],
    })
    assert dataset.targets.source == "dataset"


def test_metadata_validation_needs_no_document(definitions: dict[int, CustomField]) -> None:
    good = spec({
        "operation": "template", "field": core("title"), "template": "{period}",
        "bindings": {"period": custom(1)},
    })
    assert validate(good, definitions) == []
    assert validate(spec({
        "operation": "set", "field": custom(5), "value": "Été"
    }), definitions)[0].code == "INVALID_VALUE"
    assert validate(spec({
        "operation": "template", "field": core("title"), "template": "{period}",
        "bindings": {"period": custom(42)},
    }), definitions)[0].code == "UNKNOWN_FIELD"
    assert validate(spec({
        "operation": "clear", "field": custom(1)
    }), definitions)[0].code == "INVALID_CLEAR"


@respx.mock
def test_validate_api_does_not_fetch_a_document(client: TestClient) -> None:
    _mock_reference_endpoints(custom_fields=[
        {"id": 1, "name": "Période concernée", "data_type": "string"}
    ])
    document_route = respx.get("http://paperless.test/api/documents/100/").mock(
        side_effect=AssertionError("Validation fetched a document")
    )
    response = client.post("/api/v1/transformations/validate", json=spec({
        "operation": "set", "field": custom(1), "value": "x"
    }).model_dump(mode="json"))
    assert response.status_code == 200, response.text
    assert response.json() == {"valid": True, "issues": []}
    assert not document_route.called


@respx.mock
def test_evaluate_api_never_writes_paperless(client: TestClient) -> None:
    fields = [{"id": 1, "name": "Période concernée", "data_type": "string"}]
    _mock_reference_endpoints(custom_fields=fields)
    respx.get("http://paperless.test/api/documents/100/").mock(return_value=httpx.Response(
        200, json=_document(custom_fields=[{"field": 1, "value": "Juillet 2026"}])
    ))
    write_routes = []
    for method in ["patch", "post", "put", "delete"]:
        route = getattr(respx, method)("http://paperless.test/api/documents/100/").mock(
            side_effect=AssertionError("M6 attempted a Paperless write")
        )
        write_routes.append(route)
    response = client.post("/api/v1/transformations/documents/100/evaluate", json=spec({
        "operation": "template", "field": core("title"),
        "template": "Relevé de vacations \u2013 {Période concernée}",
        "bindings": {"Période concernée": custom(1)},
    }).model_dump(mode="json"))
    assert response.status_code == 200, response.text
    assert response.json()["changes"][0]["intended"]["raw"] == (
        "Relevé de vacations \u2013 Juillet 2026"
    )
    assert not any(route.called for route in write_routes)
