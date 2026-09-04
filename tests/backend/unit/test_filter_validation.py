"""Validation of a FilterSet, before anything is compiled or sent.

The interesting cases here are the ones where being permissive would be
*silently* wrong rather than loudly wrong: an empty text value (which
django-filter drops, widening the result set), a select label sent where an
option id belongs (which Paperless would helpfully resolve, so a renamed
label changes the filter's meaning), a monetary value arriving as a JSON
number (which loses precision before anything else gets a chance to).
"""

from __future__ import annotations

import pytest

from paperwrench.filters import CoreField
from paperwrench.filters import CustomFieldRef
from paperwrench.filters import FilterCondition
from paperwrench.filters import FilterGroup
from paperwrench.filters import FilterOperator as Op
from paperwrench.filters import FilterSet
from paperwrench.filters import GroupOperator
from paperwrench.filters import validate_filterset
from paperwrench.filters.issues import FilterIssueCode
from paperwrench.filters.issues import FilterIssueStage
from paperwrench.filters.validation import MAX_CONDITIONS
from paperwrench.filters.validation import parse_monetary
from tests.backend.unit.filter_fixtures import CATEGORIE
from tests.backend.unit.filter_fixtures import LIEN
from tests.backend.unit.filter_fixtures import MONTANT
from tests.backend.unit.filter_fixtures import NB_VACATIONS
from tests.backend.unit.filter_fixtures import OPTION_URGENT
from tests.backend.unit.filter_fixtures import PERIODE
from tests.backend.unit.filter_fixtures import PIECE_JOINTE
from tests.backend.unit.filter_fixtures import REGLEMENT
from tests.backend.unit.filter_fixtures import TAUX
from tests.backend.unit.filter_fixtures import VALIDE
from tests.backend.unit.filter_fixtures import and_group
from tests.backend.unit.filter_fixtures import catalog
from tests.backend.unit.filter_fixtures import core
from tests.backend.unit.filter_fixtures import custom
from tests.backend.unit.filter_fixtures import filterset
from tests.backend.unit.filter_fixtures import or_group

CATALOG = catalog()


def codes(*children: object) -> list[FilterIssueCode]:
    return [issue.code for issue in validate_filterset(filterset(*children), CATALOG)]


def issues(*children: object) -> list[object]:
    return list(validate_filterset(filterset(*children), CATALOG))


# ================================================================== valid
def test_a_well_formed_filter_produces_no_issues() -> None:
    assert (
        codes(
            core(CoreField.DOCUMENT_TYPE, Op.EQUALS, 3),
            custom(MONTANT, Op.GREATER_THAN, "EUR0.00"),
            custom(PERIODE, Op.IS_PRESENT),
        )
        == []
    )


def test_an_empty_filterset_is_valid() -> None:
    assert validate_filterset(FilterSet(), CATALOG) == []


# ========================================================== unknown things
def test_an_unknown_custom_field_id_is_rejected_not_ignored() -> None:
    """A deleted custom field must break the filter, never silently widen it."""
    assert codes(custom(9999, Op.EQUALS, "x")) == [FilterIssueCode.UNKNOWN_FIELD]


def test_the_unknown_field_issue_names_the_reference_the_caller_sent() -> None:
    issue = validate_filterset(filterset(custom(9999, Op.EQUALS, "x")), CATALOG)[0]
    assert issue.field == "custom_field:9999"
    assert issue.path == "root.children[0]"
    assert issue.stage is FilterIssueStage.VALIDATION


def test_an_unknown_core_field_name_is_rejected_by_the_model_itself() -> None:
    """CoreField is a closed enum, so this cannot even be constructed."""
    with pytest.raises(ValueError, match="not_a_field"):
        FilterCondition.model_validate(
            {
                "kind": "condition",
                "field": {"source": "core", "name": "not_a_field"},
                "operator": "equals",
                "value": 1,
            }
        )


def test_an_unknown_operator_is_rejected_by_the_model_itself() -> None:
    with pytest.raises(ValueError, match="sounds_like"):
        FilterCondition.model_validate(
            {
                "kind": "condition",
                "field": {"source": "core", "name": "title"},
                "operator": "sounds_like",
                "value": "x",
            }
        )


# ================================================ operator / type mismatch
def test_contains_on_a_boolean_is_refused() -> None:
    """The M4 brief's explicit example. Paperless's own table forbids it too."""
    assert codes(custom(VALIDE, Op.CONTAINS, "true")) == [FilterIssueCode.OPERATOR_NOT_ALLOWED]


def test_the_operator_issue_lists_what_is_allowed_instead() -> None:
    issue = validate_filterset(filterset(custom(VALIDE, Op.CONTAINS, "x")), CATALOG)[0]
    assert issue.details is not None
    assert issue.details["field_type"] == "boolean"
    assert "equals" in issue.details["allowed"]
    assert "contains" not in issue.details["allowed"]


def test_greater_than_on_a_select_is_refused() -> None:
    assert codes(custom(CATEGORIE, Op.GREATER_THAN, OPTION_URGENT)) == [
        FilterIssueCode.OPERATOR_NOT_ALLOWED
    ]


def test_is_empty_is_refused_on_a_monetary_field() -> None:
    """A monetary column cannot hold "", so Paperless would 400 on `exact: ""`."""
    assert codes(custom(MONTANT, Op.IS_EMPTY)) == [FilterIssueCode.OPERATOR_NOT_ALLOWED]


def test_is_empty_is_allowed_on_text_url_and_long_text() -> None:
    assert codes(custom(PERIODE, Op.IS_EMPTY)) == []
    assert codes(custom(LIEN, Op.IS_EMPTY)) == []


def test_a_document_link_only_exposes_the_missing_family_in_this_version() -> None:
    assert codes(custom(PIECE_JOINTE, Op.IS_MISSING)) == []
    assert codes(custom(PIECE_JOINTE, Op.EQUALS, 1)) == [FilterIssueCode.OPERATOR_NOT_ALLOWED]


def test_core_title_has_no_missing_family_because_the_column_is_not_nullable() -> None:
    assert codes(core(CoreField.TITLE, Op.IS_MISSING)) == [
        FilterIssueCode.OPERATOR_NOT_ALLOWED
    ]


def test_a_text_custom_field_does_have_the_missing_family() -> None:
    """Same FieldType as a core title, different field: operators are per-field."""
    assert codes(custom(PERIODE, Op.IS_MISSING)) == []


def test_tags_have_no_equals_because_set_equality_has_no_server_side_form() -> None:
    assert codes(core(CoreField.TAGS, Op.EQUALS, 1)) == [FilterIssueCode.OPERATOR_NOT_ALLOWED]


def test_dates_have_no_missing_family() -> None:
    assert codes(core(CoreField.CREATED, Op.IS_MISSING)) == [
        FilterIssueCode.OPERATOR_NOT_ALLOWED
    ]


# ========================================================== value presence
def test_a_valueless_operator_given_a_value_is_refused() -> None:
    """Someone who sent a value believes it does something. It does not."""
    assert codes(custom(MONTANT, Op.IS_MISSING, "0.00")) == [
        FilterIssueCode.VALUE_NOT_ALLOWED
    ]


def test_a_missing_value_is_refused() -> None:
    assert codes(custom(PERIODE, Op.EQUALS)) == [FilterIssueCode.VALUE_REQUIRED]


def test_an_explicit_null_value_is_refused_rather_than_read_as_is_null() -> None:
    """`equals: null` is not `is null`; guessing between them would be an invention."""
    assert codes(custom(PERIODE, Op.EQUALS, None)) == [FilterIssueCode.VALUE_REQUIRED]


# ============================================================= value types
def test_a_monetary_value_must_be_a_string_never_a_json_number() -> None:
    """A JSON float would already have lost precision before it got here."""
    assert codes(custom(MONTANT, Op.EQUALS, 10.5)) == [FilterIssueCode.VALUE_WRONG_TYPE]
    assert codes(custom(MONTANT, Op.EQUALS, 10)) == [FilterIssueCode.VALUE_WRONG_TYPE]


def test_a_monetary_string_may_carry_a_currency_prefix_or_not() -> None:
    assert codes(custom(MONTANT, Op.EQUALS, "EUR0.00")) == []
    assert codes(custom(MONTANT, Op.EQUALS, "0.00")) == []
    assert codes(custom(MONTANT, Op.EQUALS, "-12.34")) == []


def test_a_malformed_monetary_string_is_refused() -> None:
    assert codes(custom(MONTANT, Op.EQUALS, "beaucoup")) == [FilterIssueCode.VALUE_WRONG_TYPE]
    assert codes(custom(MONTANT, Op.EQUALS, "12,34")) == [FilterIssueCode.VALUE_WRONG_TYPE]


def test_parse_monetary_keeps_the_exact_decimal() -> None:
    assert str(parse_monetary("EUR0.00")) == "0.00"
    assert str(parse_monetary("1234.56")) == "1234.56"
    assert str(parse_monetary(" EUR12.30 ")) == "12.30"


def test_a_boolean_is_not_an_integer() -> None:
    """`isinstance(True, int)` is True in Python; an unguarded check would pass."""
    assert codes(custom(NB_VACATIONS, Op.EQUALS, True)) == [FilterIssueCode.VALUE_WRONG_TYPE]
    assert codes(custom(TAUX, Op.GREATER_THAN, True)) == [FilterIssueCode.VALUE_WRONG_TYPE]
    assert codes(core(CoreField.DOCUMENT_TYPE, Op.EQUALS, True)) == [
        FilterIssueCode.VALUE_WRONG_TYPE
    ]


def test_an_integer_field_refuses_a_string() -> None:
    assert codes(custom(NB_VACATIONS, Op.EQUALS, "12")) == [FilterIssueCode.VALUE_WRONG_TYPE]


def test_a_float_field_accepts_an_integer() -> None:
    assert codes(custom(TAUX, Op.GREATER_THAN, 3)) == []


def test_a_boolean_field_refuses_a_string() -> None:
    assert codes(custom(VALIDE, Op.EQUALS, "true")) == [FilterIssueCode.VALUE_WRONG_TYPE]


def test_a_date_must_be_an_iso_calendar_day() -> None:
    assert codes(custom(REGLEMENT, Op.EQUALS, "2024-02-05")) == []
    assert codes(custom(REGLEMENT, Op.EQUALS, "05/02/2024")) == [
        FilterIssueCode.VALUE_WRONG_TYPE
    ]
    assert codes(custom(REGLEMENT, Op.EQUALS, "20240205")) == [
        FilterIssueCode.VALUE_WRONG_TYPE
    ]
    assert codes(custom(REGLEMENT, Op.EQUALS, "2024-02-05T10:00:00Z")) == [
        FilterIssueCode.VALUE_WRONG_TYPE
    ]


def test_an_impossible_calendar_date_is_refused() -> None:
    assert codes(custom(REGLEMENT, Op.EQUALS, "2024-02-31")) == [
        FilterIssueCode.VALUE_WRONG_TYPE
    ]


def test_a_reference_id_must_be_a_positive_integer() -> None:
    assert codes(core(CoreField.CORRESPONDENT, Op.EQUALS, 0)) == [
        FilterIssueCode.VALUE_WRONG_TYPE
    ]
    assert codes(core(CoreField.CORRESPONDENT, Op.EQUALS, -3)) == [
        FilterIssueCode.VALUE_WRONG_TYPE
    ]


# ---------------------------------------------------------- empty strings
def test_an_empty_text_value_is_refused_because_paperless_would_drop_it() -> None:
    """django-filter skips a filter whose value is in EMPTY_VALUES.

    So `title contains ""` would not filter on an empty title - it would
    apply no title filter at all and return every document, while looking
    like it worked. That is the M1 silent-ignore failure again, and it is
    refused here rather than emitted.
    """
    assert codes(core(CoreField.TITLE, Op.CONTAINS, "")) == [FilterIssueCode.VALUE_EMPTY]


def test_an_empty_text_value_is_refused_on_custom_fields_too() -> None:
    """Even where Paperless would accept it, `is_empty` says it unambiguously."""
    assert codes(custom(PERIODE, Op.EQUALS, "")) == [FilterIssueCode.VALUE_EMPTY]


def test_the_empty_value_message_points_at_the_operator_that_does_work() -> None:
    issue = validate_filterset(filterset(core(CoreField.TITLE, Op.CONTAINS, "")), CATALOG)[0]
    assert "is empty" in issue.message
    assert "is missing" in issue.message


# ------------------------------------------------------------ select ids
def test_a_select_label_sent_instead_of_an_option_id_is_refused() -> None:
    """Paperless would accept the label and resolve it. That is the problem.

    A resolved label means renaming an option silently changes which
    documents a saved filter matches. The stored id is the identity.
    """
    assert codes(custom(CATEGORIE, Op.EQUALS, "Urgent")) == [
        FilterIssueCode.UNKNOWN_SELECT_OPTION
    ]


def test_a_known_option_id_is_accepted() -> None:
    assert codes(custom(CATEGORIE, Op.EQUALS, OPTION_URGENT)) == []


def test_an_option_id_that_no_longer_exists_is_refused() -> None:
    assert codes(custom(CATEGORIE, Op.EQUALS, "deletedOption01")) == [
        FilterIssueCode.UNKNOWN_SELECT_OPTION
    ]


def test_the_unknown_option_issue_lists_the_option_ids_that_do_exist() -> None:
    issue = validate_filterset(filterset(custom(CATEGORIE, Op.EQUALS, "Urgent")), CATALOG)[0]
    assert issue.details is not None
    assert OPTION_URGENT in issue.details["known_option_ids"]


# ------------------------------------------------------------------ lists
def test_a_list_operator_refuses_a_scalar() -> None:
    assert codes(core(CoreField.TAGS, Op.HAS_ALL_OF, 1)) == [FilterIssueCode.VALUE_WRONG_TYPE]


def test_a_scalar_operator_refuses_a_list() -> None:
    assert codes(core(CoreField.TITLE, Op.CONTAINS, ["a", "b"])) == [
        FilterIssueCode.VALUE_WRONG_TYPE
    ]


def test_an_empty_list_is_refused() -> None:
    """"has all of nothing" and "is one of nothing" have no defensible meaning."""
    assert codes(core(CoreField.TAGS, Op.HAS_ANY_OF, [])) == [FilterIssueCode.VALUE_EMPTY]
    assert codes(custom(CATEGORIE, Op.IN, [])) == [FilterIssueCode.VALUE_EMPTY]


def test_every_element_of_a_list_is_type_checked() -> None:
    assert codes(core(CoreField.TAGS, Op.HAS_ALL_OF, [1, "two", 3])) == [
        FilterIssueCode.VALUE_WRONG_TYPE
    ]


def test_a_bad_list_element_is_reported_at_its_own_index() -> None:
    issue = validate_filterset(
        filterset(core(CoreField.TAGS, Op.HAS_ALL_OF, [1, "two"])), CATALOG
    )[0]
    assert issue.path == "root.children[0].value[1]"


def test_select_option_ids_are_checked_inside_an_in_list() -> None:
    assert codes(custom(CATEGORIE, Op.IN, [OPTION_URGENT, "Urgent"])) == [
        FilterIssueCode.UNKNOWN_SELECT_OPTION
    ]


# =========================================================== tree structure
def test_a_nested_empty_group_is_refused() -> None:
    assert (
        validate_filterset(FilterSet(root=and_group(or_group())), CATALOG)[0].code
        is FilterIssueCode.EMPTY_GROUP
    )


def test_an_empty_root_group_is_not_refused() -> None:
    """"No filter" is a legitimate state; an empty *nested* group is not."""
    assert validate_filterset(FilterSet(root=FilterGroup(children=[])), CATALOG) == []


def test_a_not_node_is_structurally_valid_even_though_it_will_not_compile() -> None:
    """The two verdicts are separate: this is the compiler's business, not this stage's."""
    from paperwrench.filters import FilterNot

    assert validate_filterset(
        FilterSet(root=and_group(FilterNot(child=custom(MONTANT, Op.IS_MISSING)))), CATALOG
    ) == []


def test_a_value_error_inside_a_not_is_still_reported() -> None:
    from paperwrench.filters import FilterNot

    result = validate_filterset(
        FilterSet(root=and_group(FilterNot(child=custom(MONTANT, Op.EQUALS, 1.5)))), CATALOG
    )
    assert [issue.code for issue in result] == [FilterIssueCode.VALUE_WRONG_TYPE]
    assert result[0].path == "root.children[0].child.value"


def test_too_many_conditions_is_refused_once() -> None:
    result = validate_filterset(
        filterset(*[custom(PERIODE, Op.CONTAINS, str(i)) for i in range(MAX_CONDITIONS + 5)]),
        CATALOG,
    )
    assert [issue.code for issue in result] == [FilterIssueCode.TOO_MANY_CONDITIONS]


def test_excessive_nesting_is_refused() -> None:
    node: object = custom(PERIODE, Op.CONTAINS, "x")
    for _ in range(12):
        node = or_group(node)
    result = validate_filterset(FilterSet(root=and_group(node)), CATALOG)
    assert FilterIssueCode.TOO_DEEPLY_NESTED in [issue.code for issue in result]


def test_every_bad_condition_is_reported_not_just_the_first() -> None:
    """A builder UI must be able to flag every bad row at once."""
    assert codes(
        custom(VALIDE, Op.CONTAINS, "x"),
        custom(9999, Op.EQUALS, "x"),
        core(CoreField.TITLE, Op.CONTAINS, ""),
        custom(MONTANT, Op.EQUALS, 1.5),
    ) == [
        FilterIssueCode.OPERATOR_NOT_ALLOWED,
        FilterIssueCode.UNKNOWN_FIELD,
        FilterIssueCode.VALUE_EMPTY,
        FilterIssueCode.VALUE_WRONG_TYPE,
    ]


def test_issue_paths_address_nodes_inside_nested_groups() -> None:
    result = validate_filterset(
        FilterSet(
            root=and_group(
                core(CoreField.TITLE, Op.CONTAINS, "ok"),
                or_group(custom(PERIODE, Op.CONTAINS, "ok"), custom(VALIDE, Op.CONTAINS, "bad")),
            )
        ),
        CATALOG,
    )
    assert [issue.path for issue in result] == ["root.children[1].children[1]"]


def test_extra_keys_in_a_condition_are_rejected() -> None:
    """`extra="forbid"`: a typo'd key must not be silently ignored either."""
    with pytest.raises(ValueError, match="valeur"):
        FilterCondition.model_validate(
            {
                "kind": "condition",
                "field": {"source": "custom_field", "field_id": 1},
                "operator": "equals",
                "valeur": "oops",
            }
        )


def test_a_custom_field_reference_requires_an_id_not_a_name() -> None:
    with pytest.raises(ValueError):
        CustomFieldRef.model_validate({"source": "custom_field", "name": "Montant"})


def test_a_display_name_is_accepted_but_is_not_the_identity() -> None:
    ref = CustomFieldRef(field_id=MONTANT, display_name="Montant")
    assert ref.key == f"custom_field:{MONTANT}"
    other = CustomFieldRef(field_id=MONTANT, display_name="Renamed since")
    assert other.key == ref.key


def test_the_group_operator_enum_is_closed() -> None:
    with pytest.raises(ValueError):
        FilterGroup.model_validate({"kind": "group", "operator": "xor", "children": []})
    assert {op.value for op in GroupOperator} == {"and", "or"}
