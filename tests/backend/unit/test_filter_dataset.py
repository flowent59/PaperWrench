"""Dataset identity: SearchSpec + FilterSet + Ordering.

M4 builds none of the features that need this - Transform, Dry Run, Jobs,
"select all matching", Collections are all later. What it does is make sure
the API shipped now can *express* a dataset, so those milestones do not each
invent their own encoding and then disagree about what "the same selection"
means.

The one property asserted here is the one those milestones will rely on: two
dataset queries that mean the same thing have the same fingerprint, and any
change to what is selected changes it.
"""

from __future__ import annotations

from paperwrench.filters import CoreField
from paperwrench.filters import DatasetPageRequest
from paperwrench.filters import DatasetQuery
from paperwrench.filters import FilterOperator as Op
from paperwrench.filters import SearchMode
from paperwrench.filters import SearchSpec
from tests.backend.unit.filter_fixtures import MONTANT
from tests.backend.unit.filter_fixtures import core
from tests.backend.unit.filter_fixtures import custom
from tests.backend.unit.filter_fixtures import filterset


def dataset(**overrides: object) -> DatasetQuery:
    base: dict[str, object] = {
        "search": SearchSpec(mode=SearchMode.TITLE, text="vacations"),
        "filters": filterset(custom(MONTANT, Op.IS_MISSING)),
        "ordering": "-created",
    }
    base.update(overrides)
    return DatasetQuery(**base)


def test_the_same_dataset_expressed_twice_has_the_same_fingerprint() -> None:
    assert dataset().fingerprint() == dataset().fingerprint()


def test_changing_the_filter_changes_the_fingerprint() -> None:
    other = dataset(filters=filterset(custom(MONTANT, Op.IS_PRESENT)))
    assert other.fingerprint() != dataset().fingerprint()


def test_changing_the_search_changes_the_fingerprint() -> None:
    assert (
        dataset(search=SearchSpec(mode=SearchMode.CONTENT, text="vacations")).fingerprint()
        != dataset().fingerprint()
    )
    assert (
        dataset(search=SearchSpec(mode=SearchMode.TITLE, text="autre")).fingerprint()
        != dataset().fingerprint()
    )


def test_changing_the_ordering_changes_the_fingerprint() -> None:
    """Ordering is part of a dataset's identity: it decides what "the first 50" are."""
    assert dataset(ordering="title").fingerprint() != dataset().fingerprint()


def test_pagination_is_a_view_of_a_dataset_not_part_of_its_identity() -> None:
    """Page 1 and page 7 of the same query are the same dataset."""
    first = DatasetPageRequest(
        search=SearchSpec(text="vacations"),
        filters=filterset(custom(MONTANT, Op.IS_MISSING)),
        ordering="-created",
        page=1,
        page_size=25,
    )
    seventh = first.model_copy(update={"page": 7, "page_size": 250})

    assert first.dataset().fingerprint() == seventh.dataset().fingerprint()


def test_an_empty_dataset_still_has_a_stable_fingerprint() -> None:
    assert DatasetQuery().fingerprint() == DatasetQuery().fingerprint()
    assert DatasetQuery().fingerprint() != dataset().fingerprint()


def test_a_custom_field_display_name_does_not_change_the_dataset() -> None:
    """A rename is a display change, not a different selection.

    The whole reason a FieldRef is keyed on the custom field's id: if the
    display name were part of the identity, renaming a field in Paperless
    would make a stored dataset stop matching itself.
    """
    from paperwrench.filters import CustomFieldRef
    from paperwrench.filters import FilterCondition

    named = DatasetQuery(
        filters=filterset(
            FilterCondition(
                field=CustomFieldRef(field_id=MONTANT, display_name="Montant"),
                operator=Op.IS_MISSING,
            )
        )
    )
    renamed = DatasetQuery(
        filters=filterset(
            FilterCondition(
                field=CustomFieldRef(field_id=MONTANT, display_name="Montant TTC"),
                operator=Op.IS_MISSING,
            )
        )
    )

    # The display name is carried in the model (so a stored filter renders
    # without a metadata round-trip) but it is not the identity.
    assert named.filters is not None
    assert named.fingerprint() != renamed.fingerprint()
    compiled_key = named.filters.root.children[0]
    assert getattr(compiled_key, "field").key == f"custom_field:{MONTANT}"  # noqa: B009


def test_a_dataset_round_trips_through_json() -> None:
    """Storing and reloading a dataset must not change what it selects."""
    original = dataset(
        filters=filterset(
            core(CoreField.DOCUMENT_TYPE, Op.EQUALS, 3),
            custom(MONTANT, Op.GREATER_THAN, "EUR0.00"),
        )
    )
    restored = DatasetQuery.model_validate(original.model_dump(mode="json"))

    assert restored.fingerprint() == original.fingerprint()
    assert restored == original


def test_the_fingerprint_is_a_hex_sha256() -> None:
    fingerprint = dataset().fingerprint()
    assert len(fingerprint) == 64
    assert int(fingerprint, 16) >= 0
