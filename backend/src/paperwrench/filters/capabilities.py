"""What the frontend is allowed to build, described by the backend.

The Filter Builder must not be able to construct a filter we already know
will be refused. The obvious way to achieve that is to teach the frontend the
rules - which immediately creates two copies of the compiler's capability
matrix, drifting apart at the first change.

So the rules are served instead. ``GET /api/v1/filters/capabilities`` returns
the exact tables the validator and the compiler read (see
:mod:`paperwrench.filters.catalog`), and the frontend renders them: which
fields exist, what type each is, which operators that type allows, what shape
the value takes, and which grouping shapes compile.

Deliberately *not* a generic schema language. It describes this engine's
actual surface and nothing more; a filter builder does not need a
meta-model, it needs a list of fields and a list of operators per field.
"""

from __future__ import annotations

from pydantic import BaseModel
from pydantic import Field

from paperwrench.filters.catalog import OPERATOR_NOTES
from paperwrench.filters.catalog import OPERATOR_SEMANTICS
from paperwrench.filters.catalog import FieldCatalog
from paperwrench.filters.catalog import FieldType
from paperwrench.filters.catalog import ResolvedField
from paperwrench.filters.catalog import ValueShape
from paperwrench.filters.catalog import value_shape
from paperwrench.filters.compiler import CUSTOM_FIELD_QUERY_MAX_ATOMS
from paperwrench.filters.compiler import CUSTOM_FIELD_QUERY_MAX_DEPTH
from paperwrench.filters.model import LIST_OPERATORS
from paperwrench.filters.model import FilterOperator
from paperwrench.filters.model import SearchMode
from paperwrench.filters.validation import MAX_CONDITIONS
from paperwrench.filters.validation import MAX_DEPTH


class SelectOptionCapability(BaseModel):
    """One option of a select custom field.

    ``id`` is what a filter stores and compares; ``label`` is for display
    only. Renaming a label must never change which documents a saved filter
    matches, so the two are kept visibly separate all the way to the UI.
    """

    id: str
    label: str


class OperatorCapability(BaseModel):
    """One operator, as offered for one particular field."""

    operator: FilterOperator
    label: str
    #: Shape of the value the UI must collect. ``none`` means the operator
    #: takes no value at all and the value input must be hidden.
    value_shape: ValueShape
    #: Whether the value is a list of ``value_shape`` items.
    multi: bool = False
    #: A caveat about what this operator really does on this field type -
    #: e.g. that text equality is case-insensitive, or that monetary
    #: comparison ignores the currency code. Shown next to the operator.
    note: str | None = None


class FieldCapability(BaseModel):
    """One filterable field and everything the UI needs to render it."""

    #: Stable identity: ``core:title``, ``custom_field:17``. This is what the
    #: frontend echoes back inside a ``FieldRef``.
    key: str
    label: str
    field_type: FieldType
    source: str
    #: Present only for a custom field. The id is the identity; the label
    #: above is display only.
    custom_field_id: int | None = None
    #: Which metadata catalogue a reference value comes from, so the UI can
    #: pick the right picker.
    reference_kind: str | None = None
    select_options: list[SelectOptionCapability] = Field(default_factory=list)
    operators: list[OperatorCapability] = Field(default_factory=list)


class GroupingCapabilities(BaseModel):
    """Which tree shapes the compiler can translate.

    This is the part that stops the builder offering an OR the backend will
    refuse. Each flag is a statement about Paperless, not a PaperWrench
    preference - see :mod:`paperwrench.filters.compiler`.
    """

    #: AND over anything, at any depth.
    and_supported: bool = True
    #: OR whose branches are all custom-field conditions.
    or_custom_fields_supported: bool = True
    #: OR over core document fields. Paperless's filter parameters intersect;
    #: there is no union form.
    or_core_fields_supported: bool = False
    #: OR mixing a core field with a custom field.
    or_mixed_supported: bool = False
    #: NOT. Representable in the model, not compiled in this version.
    not_supported: bool = False
    max_conditions: int = MAX_CONDITIONS
    max_depth: int = MAX_DEPTH
    #: Paperless's own limits on the custom-field expression, which bind
    #: earlier than PaperWrench's for a custom-field-heavy filter.
    custom_field_max_depth: int = CUSTOM_FIELD_QUERY_MAX_DEPTH
    custom_field_max_conditions: int = CUSTOM_FIELD_QUERY_MAX_ATOMS


class SearchModeCapability(BaseModel):
    mode: SearchMode
    label: str
    description: str


class FilterCapabilities(BaseModel):
    """The whole contract, in one response."""

    fields: list[FieldCapability]
    grouping: GroupingCapabilities
    #: Search is not part of a FilterSet (ADR-0010); it is described here
    #: anyway because the same builder screen collects it.
    search_modes: list[SearchModeCapability]
    #: What each empty/missing operator actually means, shared across field
    #: types. Rendered as help text, so the UI never has to invent wording
    #: for a distinction the backend defines.
    operator_semantics: dict[FilterOperator, str]


OPERATOR_LABELS: dict[FilterOperator, str] = {
    FilterOperator.EQUALS: "is",
    FilterOperator.NOT_EQUALS: "is not",
    FilterOperator.IN: "is one of",
    FilterOperator.CONTAINS: "contains",
    FilterOperator.STARTS_WITH: "starts with",
    FilterOperator.ENDS_WITH: "ends with",
    FilterOperator.GREATER_THAN: "is greater than",
    FilterOperator.GREATER_OR_EQUAL: "is greater than or equal to",
    FilterOperator.LESS_THAN: "is less than",
    FilterOperator.LESS_OR_EQUAL: "is less than or equal to",
    FilterOperator.HAS_ALL_OF: "has all of",
    FilterOperator.HAS_ANY_OF: "has any of",
    FilterOperator.HAS_NONE_OF: "has none of",
    FilterOperator.IS_MISSING: "is missing",
    FilterOperator.IS_PRESENT: "is present",
    FilterOperator.IS_NULL: "is null",
    FilterOperator.HAS_VALUE: "has a value",
    FilterOperator.IS_EMPTY: "is empty",
}

SEARCH_MODE_CAPABILITIES = [
    SearchModeCapability(
        mode=SearchMode.TITLE,
        label="Title",
        description=(
            "Full-text search restricted to the document title (Paperless "
            "`title_search`). This is what a plain search box means here."
        ),
    ),
    SearchModeCapability(
        mode=SearchMode.CONTENT,
        label="Content",
        description=(
            "Full-text search over the document's extracted text (Paperless "
            "`text`)."
        ),
    ),
    SearchModeCapability(
        mode=SearchMode.ADVANCED,
        label="Advanced query",
        description=(
            "Raw Paperless query syntax, passed through unchanged (Paperless "
            "`query`). PaperWrench does not parse, rewrite or validate it, so "
            "errors in it are reported by Paperless."
        ),
    ),
]


def _operator_capability(field_type: FieldType, operator: FilterOperator) -> OperatorCapability:
    return OperatorCapability(
        operator=operator,
        label=OPERATOR_LABELS[operator],
        value_shape=value_shape(field_type, operator),
        multi=operator in LIST_OPERATORS,
        note=OPERATOR_NOTES.get((field_type, operator)),
    )


def _field_capability(resolved: ResolvedField, labels: dict[str, str]) -> FieldCapability:
    return FieldCapability(
        key=resolved.key,
        label=resolved.label,
        field_type=resolved.field_type,
        source="custom_field" if resolved.is_custom else "core",
        custom_field_id=resolved.custom_field_id,
        reference_kind=resolved.reference_kind,
        select_options=[
            SelectOptionCapability(id=option_id, label=labels.get(option_id, option_id))
            for option_id in sorted(resolved.select_option_ids)
        ],
        operators=[
            _operator_capability(resolved.field_type, operator)
            for operator in resolved.operators
        ],
    )


def build_capabilities(
    catalog: FieldCatalog, select_labels: dict[int, dict[str, str]] | None = None
) -> FilterCapabilities:
    """Describe everything this instance can filter on.

    ``select_labels`` maps a select custom field's id to its
    ``{option_id: label}`` mapping. It is passed in rather than read from the
    catalog because the catalog stores only the ids - the identity - and
    labels are strictly a rendering concern.
    """
    labels_by_field = select_labels or {}
    fields = [_field_capability(resolved, {}) for resolved in catalog.core_fields]
    fields.extend(
        _field_capability(resolved, labels_by_field.get(resolved.custom_field_id or -1, {}))
        for resolved in catalog.custom_fields
    )
    return FilterCapabilities(
        fields=fields,
        grouping=GroupingCapabilities(),
        search_modes=SEARCH_MODE_CAPABILITIES,
        operator_semantics=dict(OPERATOR_SEMANTICS),
    )
