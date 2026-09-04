"""Pure unit tests for the Paperless models and the merge primitive.

No HTTP, no I/O. These are the fastest guard rails around the most dangerous
piece of logic in the project.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from paperwrench.paperless.models import CORE_DOCUMENT_FIELDS
from paperwrench.paperless.models import ConnectionStatus
from paperwrench.paperless.models import Correspondent
from paperwrench.paperless.models import CustomField
from paperwrench.paperless.models import CustomFieldDataType
from paperwrench.paperless.models import CustomFieldValue
from paperwrench.paperless.models import CustomFieldValueKind
from paperwrench.paperless.models import Document
from paperwrench.paperless.models import DocumentType
from paperwrench.paperless.models import FieldKind
from paperwrench.paperless.models import MonetaryAmount
from paperwrench.paperless.models import Page
from paperwrench.paperless.models import StoragePath
from paperwrench.paperless.models import Tag
from paperwrench.paperless.models import field_kind
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


# --------------------------------------------------------- reference metadata
class TestCustomFieldExtraDataNull:
    """Regression: VERIFIED_LIVE on 3.1.2, ``extra_data`` can be ``null``.

    Discovered running the new metadata endpoints against the real dev
    sandbox (M2): 10 of the 13 real custom fields in the Golden Dataset -
    every string/date/boolean field - serve ``"extra_data": null``, not an
    absent key and not ``{}``. Only ``select`` (options) and ``monetary``
    (default currency) happened to carry a non-null value, which is why the
    mocked tests, which always supplied a dict literal, never caught this
    before it reached a live 500.
    """

    def test_null_extra_data_does_not_raise(self) -> None:
        field = CustomField.model_validate(
            {"id": 7, "name": "Commentaire", "data_type": "string", "extra_data": None}
        )
        assert field.extra_data == {}
        assert field.select_options == []

    def test_absent_extra_data_still_defaults_to_empty_dict(self) -> None:
        field = CustomField.model_validate(
            {"id": 7, "name": "Commentaire", "data_type": "string"}
        )
        assert field.extra_data == {}

    def test_a_real_dict_extra_data_is_unaffected(self) -> None:
        field = CustomField.model_validate(
            {
                "id": 2,
                "name": "Montant",
                "data_type": "monetary",
                "extra_data": {"default_currency": "EUR"},
            }
        )
        assert field.extra_data == {"default_currency": "EUR"}


class TestReferenceMetadataModels:
    """Tag / Correspondent / DocumentType / StoragePath - real 3.1.2 shapes."""

    def test_tag_parses_a_real_shape(self) -> None:
        tag = Tag.model_validate(
            {
                "id": 1,
                "slug": "dup",
                "name": "Dup",
                "color": "#a6cee3",
                "text_color": "#000000",
                "match": "",
                "matching_algorithm": 1,
                "is_insensitive": True,
                "is_inbox_tag": False,
                "owner": 2,
                "user_can_change": True,
                "parent": None,
                "children": [],
            }
        )
        assert tag.id == 1
        assert tag.name == "Dup"
        assert tag.user_can_change is True

    def test_correspondent_parses_a_real_shape(self) -> None:
        correspondent = Correspondent.model_validate(
            {
                "id": 1,
                "slug": "clinique-saint-roch",
                "name": "Clinique Saint-Roch",
                "match": "",
                "matching_algorithm": 1,
                "is_insensitive": True,
                "owner": 2,
                "user_can_change": True,
            }
        )
        assert correspondent.name == "Clinique Saint-Roch"

    def test_document_type_parses_a_real_shape(self) -> None:
        document_type = DocumentType.model_validate(
            {
                "id": 1,
                "slug": "releve-de-vacations",
                "name": "Relevé de vacations",
                "match": "",
                "matching_algorithm": 0,
                "is_insensitive": True,
                "document_count": 17,
                "owner": 2,
                "user_can_change": True,
            }
        )
        assert document_type.document_count == 17

    def test_storage_path_parses_a_real_shape(self) -> None:
        storage_path = StoragePath.model_validate(
            {
                "id": 1,
                "slug": "tmp-sp-shape",
                "name": "tmp-sp-shape",
                "path": "{{ title }}",
                "match": "",
                "matching_algorithm": 1,
                "is_insensitive": True,
                "owner": 2,
                "user_can_change": True,
            }
        )
        assert storage_path.path == "{{ title }}"


# ------------------------------------------------------------- core vs custom
class TestFieldKind:
    @pytest.mark.parametrize("name", sorted(CORE_DOCUMENT_FIELDS))
    def test_every_core_field_is_classified_core(self, name: str) -> None:
        assert field_kind(name) is FieldKind.CORE

    @pytest.mark.parametrize(
        "name", ["custom_field_7", "custom_field_1", "not_a_real_field", "notes"]
    )
    def test_anything_else_is_classified_custom(self, name: str) -> None:
        assert field_kind(name) is FieldKind.CUSTOM


# ------------------------------------------------------------- monetary
class TestMonetaryAmount:
    @pytest.mark.parametrize(
        ("raw", "currency", "amount"),
        [
            ("EUR450.00", "EUR", "450.00"),
            ("EUR0.00", "EUR", "0.00"),
            ("USD1234.56", "USD", "1234.56"),
            ("EUR87.05", "EUR", "87.05"),
        ],
    )
    def test_parses_the_real_312_shape(self, raw: str, currency: str, amount: str) -> None:
        parsed = MonetaryAmount.parse(raw)
        assert parsed.currency == currency
        assert parsed.amount == Decimal(amount)

    def test_amount_is_a_decimal_never_a_float(self) -> None:
        """The whole point: no float-induced rounding error.

        0.1 + 0.2 != 0.3 in float; Decimal("0.10") + Decimal("0.20") ==
        Decimal("0.30") exactly. This asserts the type, not the arithmetic,
        because the type is what guarantees the arithmetic.
        """
        parsed = MonetaryAmount.parse("EUR1234.56")
        assert isinstance(parsed.amount, Decimal)
        # mypy already knows ``amount: Decimal`` and that Decimal/float are
        # disjoint, so it flags the negative check below as unreachable. The
        # assertion is kept anyway: it is a runtime guarantee against a
        # regression that would change the field's type, not a claim mypy
        # needs to prove for us.
        amount: object = parsed.amount
        assert not isinstance(amount, float)

    def test_round_trips_back_to_the_paperless_shape(self) -> None:
        parsed = MonetaryAmount.parse("EUR1234.56")
        assert str(parsed) == "EUR1234.56"

    @pytest.mark.parametrize(
        "raw",
        [
            "EUR450,00",  # comma decimal - VERIFIED_LIVE rejected by Paperless itself
            "not-money",
            "",
            "EUR450",  # missing decimals
            "450.00",  # missing currency
        ],
    )
    def test_rejects_shapes_paperless_itself_would_reject(self, raw: str) -> None:
        with pytest.raises(ValueError, match="not a recognised monetary value"):
            MonetaryAmount.parse(raw)


# ------------------------------------------------ typed custom field values
class TestTypedCustomFieldValue:
    """absent / null / present must never collapse into one "empty" idea."""

    def _string_field(self) -> CustomField:
        return CustomField.model_validate({"id": 7, "name": "Commentaire", "data_type": "string"})

    def _monetary_field(self) -> CustomField:
        return CustomField.model_validate({"id": 2, "name": "Montant", "data_type": "monetary"})

    def _select_field(self) -> CustomField:
        return CustomField.model_validate(
            {
                "id": 8,
                "name": "Categorie",
                "data_type": "select",
                "extra_data": {
                    "select_options": [
                        {"id": "gsbRSetmXYcC2nKx", "label": "Urgent"},
                        {"id": "dhVSG5uL8oe08RGN", "label": "Normal"},
                    ]
                },
            }
        )

    def test_absent_when_the_field_is_not_on_the_document(self) -> None:
        typed = self._string_field().typed_value(None)
        assert typed.kind is CustomFieldValueKind.ABSENT
        assert typed.is_absent
        assert typed.raw is None

    def test_null_when_the_entry_is_present_with_a_none_value(self) -> None:
        typed = self._string_field().typed_value(CustomFieldValue(field=7, value=None))
        assert typed.kind is CustomFieldValueKind.NULL
        assert typed.is_null
        assert not typed.is_absent

    def test_empty_string_is_present_not_collapsed_to_empty(self) -> None:
        typed = self._string_field().typed_value(CustomFieldValue(field=7, value=""))
        assert typed.is_present
        assert typed.raw == ""

    def test_zero_is_present_not_collapsed_to_empty(self) -> None:
        typed = self._string_field().typed_value(CustomFieldValue(field=7, value=0))
        assert typed.is_present
        assert typed.raw == 0

    def test_false_is_present_not_collapsed_to_empty(self) -> None:
        typed = self._string_field().typed_value(CustomFieldValue(field=7, value=False))
        assert typed.is_present
        assert typed.raw is False

    def test_absent_null_and_empty_are_three_different_kinds(self) -> None:
        field = self._string_field()
        absent = field.typed_value(None)
        null = field.typed_value(CustomFieldValue(field=7, value=None))
        empty = field.typed_value(CustomFieldValue(field=7, value=""))
        assert len({absent.kind, null.kind, empty.kind}) == 3

    def test_monetary_value_is_parsed_into_a_decimal(self) -> None:
        typed = self._monetary_field().typed_value(
            CustomFieldValue(field=2, value="EUR1234.56")
        )
        assert typed.monetary is not None
        assert typed.monetary.amount == Decimal("1234.56")
        assert typed.raw == "EUR1234.56", "raw stays the untouched source of truth"

    def test_monetary_zero_still_present_and_parsed(self) -> None:
        typed = self._monetary_field().typed_value(CustomFieldValue(field=2, value="EUR0.00"))
        assert typed.is_present
        assert typed.monetary is not None
        assert typed.monetary.amount == Decimal("0.00")

    def test_malformed_monetary_leaves_monetary_none_but_keeps_raw(self) -> None:
        """A malformed value is not this method's job to fix or hide."""
        typed = self._monetary_field().typed_value(
            CustomFieldValue(field=2, value="EUR450,00")
        )
        assert typed.is_present
        assert typed.monetary is None
        assert typed.raw == "EUR450,00"

    def test_select_exposes_id_and_label_without_conflating_them(self) -> None:
        typed = self._select_field().typed_value(
            CustomFieldValue(field=8, value="gsbRSetmXYcC2nKx")
        )
        assert typed.select_option_id == "gsbRSetmXYcC2nKx"
        assert typed.select_label == "Urgent"
        assert typed.raw == "gsbRSetmXYcC2nKx", "raw is always the stored id, never the label"

    def test_select_with_unknown_option_id_has_no_label(self) -> None:
        """The option may have been deleted from the field definition since."""
        typed = self._select_field().typed_value(
            CustomFieldValue(field=8, value="no-longer-exists")
        )
        assert typed.select_option_id == "no-longer-exists"
        assert typed.select_label is None

    def test_document_typed_custom_fields_includes_absent_fields(self) -> None:
        document = Document.model_validate(
            {"id": 1, "title": "t", "custom_fields": [{"field": 7, "value": "hello"}]}
        )
        definitions = {7: self._string_field(), 2: self._monetary_field()}
        typed = document.typed_custom_fields(definitions)
        assert typed[7].is_present
        assert typed[7].raw == "hello"
        assert typed[2].is_absent, "field 2 is not on the document at all"


class TestOptionLabel:
    def test_resolves_a_known_option(self) -> None:
        field = CustomField.model_validate(
            {
                "id": 8,
                "name": "Categorie",
                "data_type": "select",
                "extra_data": {
                    "select_options": [{"id": "gsbRSetmXYcC2nKx", "label": "Urgent"}]
                },
            }
        )
        assert field.option_label("gsbRSetmXYcC2nKx") == "Urgent"

    def test_returns_none_for_an_unknown_option(self) -> None:
        field = CustomField.model_validate({"id": 8, "name": "Categorie", "data_type": "select"})
        assert field.option_label("does-not-exist") is None
