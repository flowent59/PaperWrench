"""Exhaustive tests for the FilterSet -> Paperless compiler.

The compiler is the highest-risk code in PaperWrench: it decides which
documents a later transformation will touch, and Paperless gives it no
feedback when it gets that wrong (an unknown filter parameter returns 200 and
the whole library - VERIFIED_LIVE, M1). These tests are therefore *pure*: no
HTTP, no fixtures, no server - just "this tree compiles to exactly these
parameters, or is refused for exactly this reason".

Two properties matter more than any individual assertion here:

* every parameter the compiler can emit is one that was read in the
  Paperless-ngx 3.1.2 source (asserted per operator below);
* an expression that cannot be compiled produces a structured refusal and
  **nothing else** - see ``test_filter_no_fallback.py`` for the guard that
  proves no request is made.
"""

from __future__ import annotations

import json

import pytest

from paperwrench.filters import CoreField
from paperwrench.filters import FilterGroup
from paperwrench.filters import FilterNot
from paperwrench.filters import FilterOperator as Op
from paperwrench.filters import FilterSet
from paperwrench.filters import GroupOperator
from paperwrench.filters import compile_filterset
from paperwrench.filters.issues import FilterIssueCode
from paperwrench.filters.issues import FilterNotCompilable
from tests.backend.unit.filter_fixtures import CATEGORIE
from tests.backend.unit.filter_fixtures import COMMENTAIRE
from tests.backend.unit.filter_fixtures import MONTANT
from tests.backend.unit.filter_fixtures import NB_VACATIONS
from tests.backend.unit.filter_fixtures import OPTION_URGENT
from tests.backend.unit.filter_fixtures import PERIODE
from tests.backend.unit.filter_fixtures import REGLEMENT
from tests.backend.unit.filter_fixtures import VALIDE
from tests.backend.unit.filter_fixtures import and_group
from tests.backend.unit.filter_fixtures import catalog
from tests.backend.unit.filter_fixtures import core
from tests.backend.unit.filter_fixtures import custom
from tests.backend.unit.filter_fixtures import filterset
from tests.backend.unit.filter_fixtures import or_group

CATALOG = catalog()


def params(*children: object) -> dict[str, str]:
    return compile_filterset(filterset(*children), CATALOG).params


def expression(*children: object) -> object:
    return compile_filterset(filterset(*children), CATALOG).custom_field_expression


def refusal(filters: FilterSet) -> list[FilterIssueCode]:
    with pytest.raises(FilterNotCompilable) as excinfo:
        compile_filterset(filters, CATALOG)
    return [issue.code for issue in excinfo.value.issues]


# ============================================================ empty filter
def test_an_empty_filterset_compiles_to_no_parameters_at_all() -> None:
    compiled = compile_filterset(FilterSet(), CATALOG)
    assert compiled.params == {}
    assert compiled.is_unfiltered is True


def test_an_empty_root_group_is_not_an_error() -> None:
    """"No filter" is a legitimate Explorer state and must compile."""
    compiled = compile_filterset(
        FilterSet(root=FilterGroup(operator=GroupOperator.OR, children=[])), CATALOG
    )
    assert compiled.params == {}


# ============================================================== core fields
def test_title_contains_maps_to_icontains() -> None:
    assert params(core(CoreField.TITLE, Op.CONTAINS, "vacations")) == {
        "title__icontains": "vacations"
    }


def test_title_equals_maps_to_iexact_because_that_is_all_paperless_offers() -> None:
    """CHAR_KWARGS has no case-sensitive `exact`; the note says so in the UI."""
    assert params(core(CoreField.TITLE, Op.EQUALS, "Relevé")) == {"title__iexact": "Relevé"}


def test_title_starts_and_ends_with() -> None:
    assert params(core(CoreField.TITLE, Op.STARTS_WITH, "scan")) == {
        "title__istartswith": "scan"
    }
    assert params(core(CoreField.TITLE, Op.ENDS_WITH, "2024")) == {"title__iendswith": "2024"}


def test_document_type_equals_maps_to_the_id_lookup() -> None:
    assert params(core(CoreField.DOCUMENT_TYPE, Op.EQUALS, 3)) == {"document_type__id": "3"}


def test_correspondent_not_equals_maps_to_the_none_lookup() -> None:
    assert params(core(CoreField.CORRESPONDENT, Op.NOT_EQUALS, 7)) == {
        "correspondent__id__none": "7"
    }


def test_reference_in_maps_to_a_sorted_deduplicated_id_list() -> None:
    """Deterministic output: these parameters get recorded, hashed and compared."""
    assert params(core(CoreField.STORAGE_PATH, Op.IN, [5, 2, 5, 9])) == {
        "storage_path__id__in": "2,5,9"
    }


def test_reference_missing_and_present_map_to_isnull() -> None:
    assert params(core(CoreField.CORRESPONDENT, Op.IS_MISSING)) == {
        "correspondent__isnull": "true"
    }
    assert params(core(CoreField.CORRESPONDENT, Op.IS_PRESENT)) == {
        "correspondent__isnull": "false"
    }


def test_archive_serial_number_equality_drops_the_exact_suffix() -> None:
    """django-filter strips `__exact` when generating the filter name."""
    assert params(core(CoreField.ARCHIVE_SERIAL_NUMBER, Op.EQUALS, 42)) == {
        "archive_serial_number": "42"
    }


def test_archive_serial_number_comparisons_and_nullability() -> None:
    assert params(core(CoreField.ARCHIVE_SERIAL_NUMBER, Op.GREATER_THAN, 10)) == {
        "archive_serial_number__gt": "10"
    }
    assert params(core(CoreField.ARCHIVE_SERIAL_NUMBER, Op.IS_MISSING)) == {
        "archive_serial_number__isnull": "true"
    }


# ------------------------------------------------------------------- tags
def test_tag_operators_map_to_the_three_distinct_paperless_semantics() -> None:
    """These are three genuinely different server-side questions.

    VERIFIED_SOURCE (``ObjectFilter``): ``tags__id__all`` loops one
    ``.filter()`` per id (has ALL), ``tags__id__in`` does a single
    ``__in`` (has ANY), ``tags__id__none`` loops ``.exclude()`` (has NONE).
    PaperWrench never simulates any of them.
    """
    assert params(core(CoreField.TAGS, Op.HAS_ALL_OF, [1, 2])) == {"tags__id__all": "1,2"}
    assert params(core(CoreField.TAGS, Op.HAS_ANY_OF, [1, 2])) == {"tags__id__in": "1,2"}
    assert params(core(CoreField.TAGS, Op.HAS_NONE_OF, [1, 2])) == {"tags__id__none": "1,2"}


def test_tags_missing_and_present_map_to_is_tagged() -> None:
    assert params(core(CoreField.TAGS, Op.IS_MISSING)) == {"is_tagged": "false"}
    assert params(core(CoreField.TAGS, Op.IS_PRESENT)) == {"is_tagged": "true"}


def test_two_has_all_of_conditions_merge_into_one_union() -> None:
    """AND of two "has all of" IS "has all of the union" - so merging is exact."""
    assert params(
        core(CoreField.TAGS, Op.HAS_ALL_OF, [1, 2]),
        core(CoreField.TAGS, Op.HAS_ALL_OF, [3]),
    ) == {"tags__id__all": "1,2,3"}


def test_two_has_any_of_conditions_are_refused_rather_than_merged() -> None:
    """"any of {A,B}" AND "any of {C,D}" is NOT "any of {A,B,C,D}".

    Merging them would silently broaden the filter, which is precisely the
    class of mistake this engine exists to make impossible.
    """
    assert refusal(
        filterset(
            core(CoreField.TAGS, Op.HAS_ANY_OF, [1, 2]),
            core(CoreField.TAGS, Op.HAS_ANY_OF, [3, 4]),
        )
    ) == [FilterIssueCode.PARAMETER_CONFLICT]


# ------------------------------------------------------------------ dates
def test_created_equality_compiles_to_an_exact_day_range() -> None:
    """DATE_KWARGS has no `exact`; `>= d AND <= d` on a DateField is exactly `== d`."""
    assert params(core(CoreField.CREATED, Op.EQUALS, "2024-02-05")) == {
        "created__gte": "2024-02-05",
        "created__lte": "2024-02-05",
    }


def test_created_comparisons_use_the_plain_date_lookups() -> None:
    assert params(core(CoreField.CREATED, Op.GREATER_OR_EQUAL, "2024-01-01")) == {
        "created__gte": "2024-01-01"
    }
    assert params(core(CoreField.CREATED, Op.LESS_THAN, "2025-01-01")) == {
        "created__lt": "2025-01-01"
    }


def test_added_and_modified_use_the_date_variants_because_they_are_datetimes() -> None:
    """`added` is a DateTimeField: `__date__lte` keeps the comparison day-granular.

    Using the bare `__lte` would compare against midnight and silently drop
    everything added later that same day.
    """
    assert params(core(CoreField.ADDED, Op.LESS_OR_EQUAL, "2024-03-01")) == {
        "added__date__lte": "2024-03-01"
    }
    assert params(core(CoreField.MODIFIED, Op.EQUALS, "2024-03-01")) == {
        "modified__date__gte": "2024-03-01",
        "modified__date__lte": "2024-03-01",
    }


# ====================================================== custom field atoms
def test_a_single_custom_condition_compiles_to_a_bare_atom() -> None:
    """No gratuitous ["AND", [...]] wrapper: it would cost a nesting level."""
    assert expression(custom(PERIODE, Op.EQUALS, "mars 2024")) == [PERIODE, "exact", "mars 2024"]


def test_the_custom_field_query_parameter_is_compact_sorted_json() -> None:
    compiled = compile_filterset(filterset(custom(PERIODE, Op.CONTAINS, "mars")), CATALOG)
    assert compiled.params["custom_field_query"] == '[1,"icontains","mars"]'
    assert json.loads(compiled.params["custom_field_query"]) == [PERIODE, "icontains", "mars"]


def test_custom_text_operators_map_to_the_paperless_string_category() -> None:
    assert expression(custom(PERIODE, Op.STARTS_WITH, "mars")) == [PERIODE, "istartswith", "mars"]
    assert expression(custom(PERIODE, Op.ENDS_WITH, "2024")) == [PERIODE, "iendswith", "2024"]
    assert expression(custom(COMMENTAIRE, Op.CONTAINS, "erreur")) == [
        COMMENTAIRE,
        "icontains",
        "erreur",
    ]


def test_custom_in_takes_a_list() -> None:
    assert expression(custom(PERIODE, Op.IN, ["mars 2024", "avril 2024"])) == [
        PERIODE,
        "in",
        ["mars 2024", "avril 2024"],
    ]


def test_select_compiles_the_stored_option_id_never_a_label() -> None:
    assert expression(custom(CATEGORIE, Op.EQUALS, OPTION_URGENT)) == [
        CATEGORIE,
        "exact",
        OPTION_URGENT,
    ]


def test_boolean_equality_sends_a_real_json_boolean() -> None:
    assert expression(custom(VALIDE, Op.EQUALS, True)) == [VALIDE, "exact", True]
    assert expression(custom(VALIDE, Op.EQUALS, False)) == [VALIDE, "exact", False]


def test_date_custom_field_comparisons() -> None:
    assert expression(custom(REGLEMENT, Op.GREATER_THAN, "2024-06-01")) == [
        REGLEMENT,
        "gt",
        "2024-06-01",
    ]


def test_integer_and_float_custom_fields() -> None:
    assert expression(custom(NB_VACATIONS, Op.LESS_OR_EQUAL, 12)) == [NB_VACATIONS, "lte", 12]


# --------------------------------------------------------------- monetary
def test_monetary_is_normalised_to_a_bare_decimal_string() -> None:
    """Sending "0.00" rather than "EUR0.00" sidesteps Paperless's prefix heuristic.

    ``MonetaryAmountField`` strips the first three characters when the value
    does not start with a digit or a minus. A normalised, unprefixed decimal
    has nothing to strip, so there is no heuristic left to get wrong.
    """
    assert expression(custom(MONTANT, Op.GREATER_THAN, "EUR0.00")) == [MONTANT, "gt", "0.00"]
    assert expression(custom(MONTANT, Op.EQUALS, "1234.56")) == [MONTANT, "exact", "1234.56"]


def test_monetary_zero_stays_an_exact_two_decimal_zero() -> None:
    """EUR0.00 is a real value, not an absence. It must survive as 0.00."""
    assert expression(custom(MONTANT, Op.EQUALS, "EUR0.00")) == [MONTANT, "exact", "0.00"]


def test_monetary_precision_is_preserved_exactly() -> None:
    """No float anywhere on this path: the digits that arrive are the digits sent."""
    assert expression(custom(MONTANT, Op.LESS_THAN, "0.10")) == [MONTANT, "lt", "0.10"]
    assert expression(custom(MONTANT, Op.EQUALS, "12345678901234.99")) == [
        MONTANT,
        "exact",
        "12345678901234.99",
    ]


def test_negative_monetary_amounts_keep_their_sign() -> None:
    assert expression(custom(MONTANT, Op.LESS_THAN, "-5.00")) == [MONTANT, "lt", "-5.00"]


# =============================================== empty / missing semantics
def test_is_missing_compiles_to_exists_false() -> None:
    """ABSENT: the document carries no instance of the field at all."""
    assert expression(custom(MONTANT, Op.IS_MISSING)) == [MONTANT, "exists", False]


def test_is_present_compiles_to_exists_true() -> None:
    assert expression(custom(MONTANT, Op.IS_PRESENT)) == [MONTANT, "exists", True]


def test_is_null_compiles_to_isnull_true_which_requires_the_field_to_exist() -> None:
    """NULL is not ABSENT.

    VERIFIED_SOURCE: the parser builds ``has_field & value__isnull=True``, so
    ``isnull`` can never match a document that does not carry the field.
    That asymmetry with ``exists`` is what keeps NULL and ABSENT separable.
    """
    assert expression(custom(MONTANT, Op.IS_NULL)) == [MONTANT, "isnull", True]


def test_has_value_compiles_to_isnull_false() -> None:
    assert expression(custom(MONTANT, Op.HAS_VALUE)) == [MONTANT, "isnull", False]


def test_is_empty_is_null_or_empty_string_and_never_absent() -> None:
    """The definition the M4 brief asked to be checked before freezing.

    ``exists=false`` is deliberately NOT part of it: a document that never had
    the field is a different thing from one that has it and left it blank.
    """
    assert expression(custom(PERIODE, Op.IS_EMPTY)) == [
        "OR",
        [[PERIODE, "isnull", True], [PERIODE, "exact", ""]],
    ]


def test_the_five_states_produce_five_different_expressions() -> None:
    """ABSENT / NULL / "" / has-a-value / has-an-instance are all distinguishable."""
    produced = {
        json.dumps(expression(custom(PERIODE, operator)))
        for operator in (Op.IS_MISSING, Op.IS_PRESENT, Op.IS_NULL, Op.HAS_VALUE, Op.IS_EMPTY)
    }
    assert len(produced) == 5


# ============================================================ AND / OR / NOT
def test_core_and_custom_conditions_combine_with_and() -> None:
    compiled = compile_filterset(
        filterset(
            core(CoreField.DOCUMENT_TYPE, Op.EQUALS, 3),
            custom(MONTANT, Op.IS_MISSING),
            custom(PERIODE, Op.IS_PRESENT),
        ),
        CATALOG,
    )
    assert compiled.params["document_type__id"] == "3"
    assert compiled.custom_field_expression == [
        "AND",
        [[MONTANT, "exists", False], [PERIODE, "exists", True]],
    ]


def test_an_or_group_of_custom_fields_is_supported() -> None:
    compiled = compile_filterset(
        FilterSet(
            root=and_group(
                core(CoreField.DOCUMENT_TYPE, Op.EQUALS, 3),
                or_group(custom(MONTANT, Op.IS_MISSING), custom(VALIDE, Op.EQUALS, True)),
            )
        ),
        CATALOG,
    )
    assert compiled.custom_field_expression == [
        "OR",
        [[MONTANT, "exists", False], [VALIDE, "exact", True]],
    ]


def test_a_root_level_or_of_custom_fields_is_supported() -> None:
    compiled = compile_filterset(
        FilterSet(root=or_group(custom(MONTANT, Op.IS_MISSING), custom(PERIODE, Op.IS_MISSING))),
        CATALOG,
    )
    assert compiled.params == {
        "custom_field_query": '["OR",[[2,"exists",false],[1,"exists",false]]]'
    }


def test_nested_custom_or_inside_custom_and_is_supported() -> None:
    compiled = compile_filterset(
        FilterSet(
            root=and_group(
                custom(VALIDE, Op.EQUALS, True),
                or_group(custom(MONTANT, Op.IS_MISSING), custom(MONTANT, Op.EQUALS, "0.00")),
            )
        ),
        CATALOG,
    )
    assert compiled.custom_field_expression == [
        "AND",
        [
            [VALIDE, "exact", True],
            ["OR", [[MONTANT, "exists", False], [MONTANT, "exact", "0.00"]]],
        ],
    ]


def test_an_or_over_core_fields_is_refused() -> None:
    assert refusal(
        FilterSet(
            root=or_group(
                core(CoreField.TITLE, Op.CONTAINS, "a"),
                core(CoreField.DOCUMENT_TYPE, Op.EQUALS, 3),
            )
        )
    ) == [FilterIssueCode.CORE_OR_UNSUPPORTED]


def test_an_or_mixing_a_core_field_and_a_custom_field_is_refused() -> None:
    """The M4 acceptance scenario's step 11-12.

    Paperless evaluates ``custom_field_query`` separately and can only
    intersect it with the core parameters. There is no union, so there is no
    honest compilation - and approximating one is forbidden (ADR-0007).
    """
    assert refusal(
        FilterSet(
            root=or_group(
                core(CoreField.DOCUMENT_TYPE, Op.EQUALS, 3),
                custom(MONTANT, Op.IS_MISSING),
            )
        )
    ) == [FilterIssueCode.MIXED_OR_UNSUPPORTED]


def test_a_nested_mixed_or_inside_an_and_is_refused_too() -> None:
    assert refusal(
        FilterSet(
            root=and_group(
                core(CoreField.TITLE, Op.CONTAINS, "x"),
                or_group(core(CoreField.TITLE, Op.CONTAINS, "y"), custom(MONTANT, Op.IS_MISSING)),
            )
        )
    ) == [FilterIssueCode.MIXED_OR_UNSUPPORTED]


def test_not_is_refused_in_this_version() -> None:
    assert refusal(FilterSet(root=and_group(FilterNot(child=custom(MONTANT, Op.IS_MISSING))))) == [
        FilterIssueCode.NEGATION_UNSUPPORTED
    ]


def test_not_nested_inside_a_custom_or_is_refused() -> None:
    assert FilterIssueCode.NEGATION_UNSUPPORTED in refusal(
        FilterSet(
            root=or_group(
                custom(VALIDE, Op.EQUALS, True),
                FilterNot(child=custom(MONTANT, Op.IS_MISSING)),
            )
        )
    )


def test_every_refusal_reason_is_reported_not_just_the_first() -> None:
    codes = refusal(
        FilterSet(
            root=and_group(
                or_group(
                    core(CoreField.TITLE, Op.CONTAINS, "a"),
                    core(CoreField.TITLE, Op.CONTAINS, "b"),
                ),
                or_group(core(CoreField.TITLE, Op.CONTAINS, "c"), custom(MONTANT, Op.IS_MISSING)),
            )
        )
    )
    assert codes == [
        FilterIssueCode.CORE_OR_UNSUPPORTED,
        FilterIssueCode.MIXED_OR_UNSUPPORTED,
    ]


# ========================================================== normalisation
def test_a_single_child_group_is_equivalent_to_its_child_whatever_the_operator() -> None:
    """OR(x) == AND(x) == x. An exact rewrite, not a guess."""
    for operator in (GroupOperator.AND, GroupOperator.OR):
        compiled = compile_filterset(
            FilterSet(
                root=FilterGroup(
                    operator=operator, children=[core(CoreField.TITLE, Op.CONTAINS, "a")]
                )
            ),
            CATALOG,
        )
        assert compiled.params == {"title__icontains": "a"}


def test_a_single_child_core_or_group_is_not_refused() -> None:
    """A one-branch OR is not really an OR, so refusing it would be wrong."""
    compiled = compile_filterset(
        FilterSet(root=and_group(or_group(core(CoreField.DOCUMENT_TYPE, Op.EQUALS, 3)))),
        CATALOG,
    )
    assert compiled.params == {"document_type__id": "3"}


def test_nested_and_groups_are_flattened_by_associativity() -> None:
    compiled = compile_filterset(
        FilterSet(
            root=and_group(
                core(CoreField.DOCUMENT_TYPE, Op.EQUALS, 3),
                and_group(
                    core(CoreField.TITLE, Op.CONTAINS, "vac"), custom(MONTANT, Op.IS_MISSING)
                ),
            )
        ),
        CATALOG,
    )
    assert compiled.params["document_type__id"] == "3"
    assert compiled.params["title__icontains"] == "vac"
    assert compiled.custom_field_expression == [MONTANT, "exists", False]


def test_a_bare_condition_as_the_root_is_treated_as_an_and_of_one() -> None:
    compiled = compile_filterset(
        FilterSet(root=and_group(core(CoreField.TITLE, Op.CONTAINS, "a"))), CATALOG
    )
    assert compiled.params == {"title__icontains": "a"}


# ====================================================== parameter conflicts
def test_two_conditions_on_the_same_parameter_with_different_values_are_refused() -> None:
    """A query string carries one value per key; one condition would vanish."""
    assert refusal(
        filterset(
            core(CoreField.TITLE, Op.CONTAINS, "a"),
            core(CoreField.TITLE, Op.CONTAINS, "b"),
        )
    ) == [FilterIssueCode.PARAMETER_CONFLICT]


def test_an_identical_condition_stated_twice_is_idempotent_not_a_conflict() -> None:
    assert params(
        core(CoreField.DOCUMENT_TYPE, Op.EQUALS, 3),
        core(CoreField.DOCUMENT_TYPE, Op.EQUALS, 3),
    ) == {"document_type__id": "3"}


def test_different_lookups_on_the_same_field_are_different_parameters() -> None:
    assert params(
        core(CoreField.TITLE, Op.CONTAINS, "vac"),
        core(CoreField.TITLE, Op.STARTS_WITH, "scan"),
    ) == {"title__icontains": "vac", "title__istartswith": "scan"}


def test_a_range_built_from_two_date_conditions_compiles() -> None:
    assert params(
        core(CoreField.CREATED, Op.GREATER_OR_EQUAL, "2024-01-01"),
        core(CoreField.CREATED, Op.LESS_OR_EQUAL, "2024-12-31"),
    ) == {"created__gte": "2024-01-01", "created__lte": "2024-12-31"}


# ============================================== Paperless complexity limits
def test_more_than_twenty_custom_atoms_is_refused_locally() -> None:
    """Paperless caps custom_field_query at 20 atoms; we refuse before asking."""
    codes = refusal(filterset(*[custom(PERIODE, Op.CONTAINS, str(i)) for i in range(21)]))
    assert codes == [FilterIssueCode.CUSTOM_FIELD_QUERY_TOO_COMPLEX]


def test_is_empty_counts_as_two_atoms_towards_the_limit() -> None:
    """It expands to an OR of two, so eleven of them exceed twenty."""
    codes = refusal(filterset(*[custom(PERIODE, Op.IS_EMPTY) for _ in range(11)]))
    assert codes == [FilterIssueCode.CUSTOM_FIELD_QUERY_TOO_COMPLEX]


def test_exactly_twenty_custom_atoms_still_compiles() -> None:
    compiled = compile_filterset(
        filterset(*[custom(PERIODE, Op.CONTAINS, str(i)) for i in range(20)]), CATALOG
    )
    assert compiled.custom_field_expression[0] == "AND"  # type: ignore[index]


# ================================================================= ordering
def test_the_compiler_never_emits_an_ordering_parameter() -> None:
    """Ordering is not a filter and must not leak into the compiled query.

    It is validated and applied separately (M3's allowlist), so that the
    dataset's identity and its presentation stay distinguishable.
    """
    compiled = compile_filterset(
        filterset(
            core(CoreField.TITLE, Op.CONTAINS, "a"),
            custom(MONTANT, Op.IS_MISSING),
            core(CoreField.TAGS, Op.HAS_ANY_OF, [1]),
        ),
        CATALOG,
    )
    assert "ordering" not in compiled.params
    assert not any(key.startswith("page") for key in compiled.params)
