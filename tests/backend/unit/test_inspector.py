"""Inspector API contract and zero-write failures, with mocked Paperless."""

import json
from copy import deepcopy
from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from paperwrench.inspector import CustomChange
from paperwrench.inspector import validate_custom_change
from paperwrench.paperless.errors import PaperlessValidationError
from paperwrench.paperless.models import CustomField
from paperwrench.paperless.models import Document
from paperwrench.paperless.mutations import revision
from tests.backend.unit.test_api_documents import _document
from tests.backend.unit.test_api_documents import _mock_reference_endpoints

BASE = "http://paperless.test/api/documents/100/"
PATH = "/api/v1/documents/100"
FIELDS = [
    {"id": 1, "name": "Période concernée", "data_type": "string"},
    {"id": 2, "name": "Montant", "data_type": "monetary"},
    {"id": 3, "name": "Validé", "data_type": "boolean"},
    {
        "id": 4,
        "name": "Option",
        "data_type": "select",
        "extra_data": {
            "select_options": [{"id": "opaque-id", "label": "Label"}],
        },
    },
]


def setup_document(**overrides: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    _mock_reference_endpoints(custom_fields=FIELDS)
    state = _document(
        custom_fields=[
            {"field": 1, "value": "Janvier"},
            {"field": 2, "value": "EUR0.00"},
            {"field": 3, "value": False},
            {"field": 99, "value": "unknown preserved"},
        ],
        **overrides,
    )
    patches: list[dict[str, Any]] = []
    respx.get(BASE).mock(side_effect=lambda _: httpx.Response(200, json=state))

    def write(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        patches.append(deepcopy(payload))
        state.update(payload)
        state["title"] = str(state["title"]).strip()
        return httpx.Response(200, json=state)

    respx.patch(BASE).mock(side_effect=write)
    return state, patches


def request_for(detail: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    return {
        "expected_revision": detail["revision"],
        "catalog_revision": detail["catalog_revision"],
        "core": {"title": "  normalized  "},
        "custom_changes": [],
        **overrides,
    }


@respx.mock
def test_read_and_combined_write_normalize_preserve_and_capture(client: TestClient) -> None:
    state, patches = setup_document()
    before = deepcopy(state)
    detail = client.get(PATH).json()
    assert detail["correspondent"] == {"id": 7, "name": None}
    assert detail["custom_fields"][1]["monetary"]["amount"] == "0.00"
    assert detail["custom_fields"][-1]["field_id"] == 99
    response = client.patch(
        PATH,
        json=request_for(
            detail,
            custom_changes=[
                {"field_id": 1, "kind": "present", "value": "Février"},
            ],
            acknowledge_external_race=True,
        ),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(patches) == 1
    assert body["before"]["title"] == before["title"]
    assert body["intended"]["title"] == "  normalized  "
    assert body["document"]["title"] == "normalized"
    assert body["document"]["revision"] != detail["revision"]
    assert state["custom_fields"][1:] == before["custom_fields"][1:]
    assert body["external_atomicity"] is False
    assert body["durable_history"] is False


@pytest.mark.parametrize(
    "changes",
    [
        {"custom_fields": []},
        {"core": {"custom_fields": []}},
        {"core": {"owner": 1}},
        {"core": {"title": None}},
        {"core": {"title": "   "}},
        {"core": {"tags": [True]}},
        {"core": {"created": "2024-02-30"}},
        {"core": {"archive_serial_number": False}},
        {"core": {}},
        {"custom_changes": [{"field_id": 1, "kind": "present", "value": "x"}]},
        {
            "custom_changes": [{"field_id": 3, "kind": "present", "value": 0}],
            "acknowledge_external_race": True,
        },
        {
            "custom_changes": [{"field_id": 4, "kind": "present", "value": "Label"}],
            "acknowledge_external_race": True,
        },
        {
            "custom_changes": [{"field_id": 2, "kind": "present", "value": 0.1}],
            "acknowledge_external_race": True,
        },
        {"custom_changes": [{"field_id": 1, "kind": "present"}], "acknowledge_external_race": True},
        {
            "custom_changes": [{"field_id": 1, "kind": "absent", "value": "discarded?"}],
            "acknowledge_external_race": True,
        },
        {
            "custom_changes": [{"field_id": 1, "kind": "null"}] * 2,
            "acknowledge_external_race": True,
        },
    ],
)
@respx.mock
def test_invalid_payloads_never_patch(client: TestClient, changes: dict[str, Any]) -> None:
    _, patches = setup_document()
    detail = client.get(PATH).json()
    response = client.patch(PATH, json=request_for(detail, **changes))
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert not patches


@pytest.mark.parametrize("kind,value", [("present", ""), ("null", None), ("absent", None)])
@respx.mock
def test_empty_null_absent_operations(client: TestClient, kind: str, value: Any) -> None:
    state, patches = setup_document()
    detail = client.get(PATH).json()
    change = {"field_id": 1, "kind": kind, "value": value}
    response = client.patch(
        PATH,
        json=request_for(detail, core={}, custom_changes=[change], acknowledge_external_race=True),
    )
    assert response.status_code == 200, response.text
    values = {entry["field"]: entry["value"] for entry in state["custom_fields"]}
    if kind == "absent":
        assert 1 not in values
    else:
        assert values[1] == value
    assert values[2] == "EUR0.00" and values[3] is False and 99 in values
    assert len(patches) == 1


@pytest.mark.parametrize("permission", [False, None])
@respx.mock
def test_permission_fail_closed(client: TestClient, permission: bool | None) -> None:
    _, patches = setup_document(user_can_change=permission)
    detail = client.get(PATH).json()
    response = client.patch(PATH, json=request_for(detail))
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PAPERLESS_FORBIDDEN"
    assert not patches


@respx.mock
def test_stale_unrelated_field_and_permission_revocation(client: TestClient) -> None:
    state, patches = setup_document()
    detail = client.get(PATH).json()
    state["custom_fields"].append({"field": 88, "value": "external"})
    response = client.patch(PATH, json=request_for(detail))
    assert response.status_code == 409
    assert response.json()["error"]["retryable"] is False
    state["user_can_change"] = False
    assert client.patch(PATH, json=request_for(detail)).status_code == 403
    assert not patches


@pytest.mark.parametrize(
    "status,code",
    [(401, "PAPERLESS_UNAUTHORIZED"), (403, "PAPERLESS_FORBIDDEN"), (404, "NOT_FOUND")],
)
@respx.mock
def test_upstream_permissions_keep_distinct_codes(
    client: TestClient, status: int, code: str
) -> None:
    _, patches = setup_document()
    detail = client.get(PATH).json()
    respx.get(BASE).mock(return_value=httpx.Response(status, json={"detail": "private"}))
    response = client.patch(PATH, json=request_for(detail))
    assert response.json()["error"]["code"] == code
    assert not patches


@respx.mock
def test_catalog_change_is_a_conflict(client: TestClient) -> None:
    _, patches = setup_document()
    detail = client.get(PATH).json()
    response = client.patch(
        PATH,
        json=request_for(
            detail,
            catalog_revision="0" * 64,
            custom_changes=[{"field_id": 1, "kind": "present", "value": "x"}],
            acknowledge_external_race=True,
        ),
    )
    assert response.status_code == 409
    assert not patches


@pytest.mark.parametrize(
    "core,custom",
    [
        ({"correspondent": 999}, []),
        ({"document_type": 999}, []),
        ({"storage_path": 999}, []),
        ({"tags": [999]}, []),
        ({}, [{"field_id": 999, "kind": "null"}]),
    ],
)
@respx.mock
def test_unknown_metadata_is_validation_not_missing_document(
    client: TestClient,
    core: dict[str, Any],
    custom: list[dict[str, Any]],
) -> None:
    _, patches = setup_document()
    detail = client.get(PATH).json()
    response = client.patch(
        PATH,
        json=request_for(
            detail,
            core=core,
            custom_changes=custom,
            acknowledge_external_race=True,
        ),
    )
    assert response.status_code == 422
    assert not patches


@pytest.mark.parametrize("as_key", [False, True])
@respx.mock
def test_invalid_input_never_echoes_token(client: TestClient, as_key: bool) -> None:
    setup_document()
    detail = client.get(PATH).json()
    core = {"test-token-abcdef123456": []} if as_key else {"tags": ["test-token-abcdef123456"]}
    response = client.patch(PATH, json=request_for(detail, core=core))
    assert response.status_code == 422
    assert "test-token-abcdef123456" not in response.text


@pytest.mark.parametrize(
    "data_type,value",
    [
        ("string", ""),
        ("boolean", False),
        ("integer", 0),
        ("monetary", "EUR0.00"),
        ("monetary", "EUR12345678901234567890.12"),
        ("date", "2024-02-29"),
        ("select", "opaque-id"),
        ("longtext", "ligne\naccents é"),
    ],
)
def test_typed_validation_keeps_values(data_type: str, value: Any) -> None:
    field = CustomField.model_validate(
        {
            "id": 1,
            "name": "field",
            "data_type": data_type,
            "extra_data": {"select_options": [{"id": "opaque-id", "label": "Label"}]},
        }
    )
    change = CustomChange(field_id=1, kind="present", value=value)
    validate_custom_change(change, field)
    assert change.value == value
    assert type(change.value) is type(value)


@pytest.mark.parametrize("data_type", ["url", "documentlink", "float"])
def test_unsupported_types_are_read_only(data_type: str) -> None:
    field = CustomField.model_validate({"id": 1, "name": "field", "data_type": data_type})
    with pytest.raises(PaperlessValidationError):
        validate_custom_change(CustomChange(field_id=1, kind="absent"), field)


def test_revision_distinguishes_false_zero_null_and_absence() -> None:
    documents = [
        Document(id=1, title="x", custom_fields=values)
        for values in [
            [],
            [{"field": 1, "value": None}],
            [{"field": 1, "value": False}],
            [{"field": 1, "value": 0}],
            [{"field": 1, "value": ""}],
        ]
    ]
    assert len({revision(document) for document in documents}) == 5
