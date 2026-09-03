"""Pure unit tests for the Paperless models and the merge primitive.

No HTTP, no I/O. These are the fastest guard rails around the most dangerous
piece of logic in the project.
"""

from __future__ import annotations

import pytest

from paperwrench.paperless.models import ConnectionStatus
from paperwrench.paperless.models import CustomField
from paperwrench.paperless.models import CustomFieldDataType
from paperwrench.paperless.models import CustomFieldValue
from paperwrench.paperless.models import Document
from paperwrench.paperless.models import Page
from paperwrench.paperless.models import merge_custom_fields


class TestMergeCustomFields:
    """The read-modify-write primitive.

    Reference payloads below are copied from real 3.1.2 responses.
    """

    def test_preserves_fields_absent_from_the_update(self) -> None:
        existing = [
            {"field": 1, "value": "Janvier 2024"},
            {"field": 2, "value": "EUR450.00"},
            {"field": 3, "value": "REF-001"},
            {"field": 4, "value": "Hopital Saint-Joseph"},
            {"field": 7, "value": "Valeur d'origine"},
        ]
        merged = merge_custom_fields(existing, [{"field": 2, "value": "EUR999.99"}])

        assert len(merged) == 5, "omitting a field from the merge would delete it upstream"
        assert {item["field"] for item in merged} == {1, 2, 3, 4, 7}
        assert next(i for i in merged if i["field"] == 2)["value"] == "EUR999.99"
        assert next(i for i in merged if i["field"] == 7)["value"] == "Valeur d'origine"

    def test_appends_a_field_the_document_did_not_have(self) -> None:
        merged = merge_custom_fields(
            [{"field": 1, "value": "a"}], [{"field": 9, "value": "new"}]
        )
        assert {item["field"] for item in merged} == {1, 9}

    def test_accepts_model_instances_on_both_sides(self) -> None:
        merged = merge_custom_fields(
            [CustomFieldValue(field=1, value="old"), CustomFieldValue(field=2, value="keep")],
            [CustomFieldValue(field=1, value="new")],
        )
        by_id = {item["field"]: item["value"] for item in merged}
        assert by_id == {1: "new", 2: "keep"}

    def test_does_not_mutate_its_arguments(self) -> None:
        existing = [{"field": 1, "value": "original"}]
        updates = [{"field": 1, "value": "changed"}]
        merge_custom_fields(existing, updates)
        assert existing == [{"field": 1, "value": "original"}]
        assert updates == [{"field": 1, "value": "changed"}]

    def test_empty_update_returns_the_existing_state_unchanged(self) -> None:
        existing = [{"field": 1, "value": "a"}, {"field": 2, "value": "b"}]
        merged = merge_custom_fields(existing, [])
        assert {i["field"]: i["value"] for i in merged} == {1: "a", 2: "b"}

    def test_empty_existing_returns_only_the_updates(self) -> None:
        merged = merge_custom_fields([], [{"field": 5, "value": "x"}])
        assert merged == [{"field": 5, "value": "x"}]

    def test_string_field_ids_are_normalised_to_int(self) -> None:
        merged = merge_custom_fields([{"field": "1", "value": "a"}], [{"field": 1, "value": "b"}])
        assert len(merged) == 1, "'1' and 1 must be the same field"
        assert merged[0]["value"] == "b"

    @pytest.mark.parametrize("value", ["", None, False, 0, "EUR0.00"])
    def test_falsy_values_are_preserved_not_dropped(self, value: object) -> None:
        """Empty string, null, false and zero are all legitimate stored values.

        VERIFIED_LIVE: 3.1.2 round-trips '' and null distinctly, and EUR0.00
        is a perfectly valid monetary amount. A truthiness test anywhere in
        the merge would silently delete these.
        """
        merged = merge_custom_fields([{"field": 1, "value": "old"}], [{"field": 1, "value": value}])
        assert len(merged) == 1
        assert merged[0]["value"] == value

    def test_last_update_wins_for_a_duplicated_field(self) -> None:
        merged = merge_custom_fields(
            [], [{"field": 1, "value": "first"}, {"field": 1, "value": "second"}]
        )
        assert merged == [{"field": 1, "value": "second"}]


class TestDocument:
    def test_parses_a_real_312_payload_and_ignores_unknown_keys(self) -> None:
        # Trimmed from an actual 3.1.2 response.
        payload = {
            "id": 1,
            "title": "probe-hazard-doc",
            "correspondent": None,
            "document_type": None,
            "storage_path": None,
            "tags": [],
            "created": "2026-09-03T00:00:00Z",
            "modified": "2026-09-03T05:52:00Z",
            "added": "2026-09-03T05:50:00Z",
            "archive_serial_number": None,
            "original_file_name": "probe.txt",
            "owner": 2,
            "user_can_change": True,
            "deleted_at": None,
            "custom_fields": [
                {"field": 2, "value": "EUR123.45"},
                {"field": 1, "value": "Fevrier 2024"},
            ],
            # Keys we deliberately do not model:
            "is_shared_by_requester": False,
            "page_count": 1,
            "versions": [],
            "root_document": None,
            "duplicate_documents": [],
            "notes": [],
            "content": "Releve de vacations",
            "mime_type": "text/plain",
        }
        document = Document.model_validate(payload)
        assert document.id == 1
        assert document.title == "probe-hazard-doc"
        assert document.user_can_change is True
        assert document.custom_field_map == {2: "EUR123.45", 1: "Fevrier 2024"}

    def test_minimal_payload(self) -> None:
        document = Document.model_validate({"id": 7, "title": "x"})
        assert document.custom_fields == []
        assert document.custom_field_map == {}


class TestCustomField:
    def test_select_options_expose_ids(self) -> None:
        # Real 3.1.2 shape: opaque server-generated string ids.
        field = CustomField.model_validate(
            {
                "id": 8,
                "name": "Categorie",
                "data_type": "select",
                "extra_data": {
                    "select_options": [
                        {"label": "Urgent", "id": "gsbRSetmXYcC2nKx"},
                        {"label": "Normal", "id": "dhVSG5uL8oe08RGN"},
                    ]
                },
            }
        )
        assert field.data_type is CustomFieldDataType.SELECT
        assert [o["id"] for o in field.select_options] == ["gsbRSetmXYcC2nKx", "dhVSG5uL8oe08RGN"]

    def test_non_select_field_has_no_options(self) -> None:
        field = CustomField.model_validate({"id": 1, "name": "Montant", "data_type": "monetary"})
        assert field.select_options == []


class TestPage:
    def test_v10_envelope(self) -> None:
        page = Page[Document].model_validate(
            {"count": 1, "next": None, "previous": None, "results": [{"id": 1, "title": "t"}]}
        )
        assert page.count == 1
        assert page.results[0].title == "t"

    def test_v9_all_key_is_ignored_rather_than_relied_upon(self) -> None:
        """v9 adds `all`. We must parse fine without ever reading it."""
        page = Page[Document].model_validate(
            {"count": 1, "next": None, "previous": None, "all": [1], "results": []}
        )
        assert not hasattr(page, "all")


class TestConnectionStatus:
    def test_carries_no_secret_field(self) -> None:
        """Structural guard: this model is serialised straight to the browser."""
        forbidden = {"token", "paperless_token", "authorization", "auth_token", "password"}
        assert not (set(ConnectionStatus.model_fields) & forbidden)
