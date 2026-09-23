"""What the Filter Engine knows how to ask, and about which fields.

This module is the single source of truth for "field -> type -> allowed
operators". The API serves it verbatim at ``GET /api/v1/filters/capabilities``
so the frontend can *render* the rules instead of reimplementing them; the
validator and the compiler read the same tables, so a filter the UI offers is
by construction a filter the backend accepts.

Everything here is a pure, in-memory snapshot. Building a :class:`FieldCatalog`
reads the Metadata Registry (which is itself a TTL cache over Paperless), and
after that the validator and compiler never do I/O - which is what makes the
compiler exhaustively unit-testable and guarantees that a rejected filter costs
**zero** requests to Paperless.

Provenance of the operator matrix
---------------------------------

Every operator below exists because it was read in the Paperless-ngx 3.1.2
source, not because it seemed reasonable:

* Core lookups come from ``DocumentFilterSet.Meta.fields`` plus the explicitly
  declared filters (``documents/filters.py``). ``CHAR_KWARGS`` is
  ``istartswith/iendswith/icontains/iexact`` - note the absence of a
  case-*sensitive* exact match, which is why ``EQUALS`` on a title is
  documented as case-insensitive rather than quietly pretending otherwise.
* Custom-field operators come from ``CustomFieldQueryParser.EXPR_BY_CATEGORY``
  and ``SUPPORTED_EXPR_CATEGORIES``. That table is what makes ``contains`` on a
  Boolean an error rather than a silently-ignored parameter.

A type/operator pair Paperless supports but PaperWrench does not expose is a
deliberate omission (``range``, ``documentlink``'s ``contains``, date component
lookups like ``year__exact``). Nothing is exposed until its translation has been
read *and* pinned by a test.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from paperwrench.filters.model import CoreField
from paperwrench.filters.model import CoreFieldRef
from paperwrench.filters.model import CustomFieldRef
from paperwrench.filters.model import FilterOperator
from paperwrench.paperless.models import CustomField
from paperwrench.paperless.models import CustomFieldDataType


class FieldType(StrEnum):
    """The semantic type of a filterable field, as the UI sees it.

    Wider than :class:`~paperwrench.paperless.models.CustomFieldDataType`
    because it also covers the core fields, which have shapes custom fields
    do not have (a set of tags, a foreign-key reference).
    """

    TEXT = "text"
    LONG_TEXT = "long_text"
    URL = "url"
    MONETARY = "monetary"
    DATE = "date"
    DATETIME = "datetime"
    INTEGER = "integer"
    FLOAT = "float"
    BOOLEAN = "boolean"
    SELECT = "select"
    DOCUMENT_LINK = "document_link"
    #: A single foreign key to a metadata object (correspondent, document
    #: type, storage path). Its value is that object's id.
    REFERENCE = "reference"
    #: The many-to-many tag set. Its operators are set operators, never
    #: equality - see the tag notes below.
    TAG_SET = "tag_set"


class ValueShape(StrEnum):
    """What a condition's ``value`` must look like once validated.

    Kept separate from :class:`FieldType` because the same field type takes
    different value shapes for different operators (``EQUALS`` on a select
    takes one option id; ``IN`` takes a list of them), and because the
    frontend needs to know which input widget to render.
    """

    NONE = "none"
    TEXT = "text"
    INTEGER = "integer"
    FLOAT = "float"
    #: An exact decimal carried as a **string**. Never a JSON number: see
    #: :mod:`paperwrench.filters.validation`.
    DECIMAL = "decimal"
    DATE = "date"
    BOOLEAN = "boolean"
    #: A stored select-option id (never a label).
    SELECT_OPTION = "select_option"
    #: The id of a metadata object.
    REFERENCE_ID = "reference_id"


#: Field types whose values live in a ``CharField``/``TextField`` on the
#: Paperless side and can therefore genuinely hold the empty string. Only
#: these expose ``IS_EMPTY``.
#:
#: VERIFIED_SOURCE (3.1.2): ``CustomFieldQueryParser._get_serializer_field``
#: sets ``allow_blank = True`` only for ``CharField``-derived value fields.
#: Asking ``exact: ""`` of a Monetary/Date/Integer/Boolean/Select field is a
#: 400 from Paperless, not an empty match - so offering ``IS_EMPTY`` there
#: would be offering a guaranteed error.
TEXT_SHAPED_TYPES = frozenset({FieldType.TEXT, FieldType.LONG_TEXT, FieldType.URL})


_MISSING_FAMILY = (
    FilterOperator.IS_MISSING,
    FilterOperator.IS_PRESENT,
    FilterOperator.IS_NULL,
    FilterOperator.HAS_VALUE,
)

_COMPARISON = (
    FilterOperator.GREATER_THAN,
    FilterOperator.GREATER_OR_EQUAL,
    FilterOperator.LESS_THAN,
    FilterOperator.LESS_OR_EQUAL,
)

#: Custom-field data type -> the operators the Filter Engine exposes for it.
#:
#: Keyed by :class:`FieldType`, but only the types a *custom field* can have
#: appear: ``REFERENCE``, ``TAG_SET`` and ``DATETIME`` exist only as core
#: fields and get their operators from :data:`CORE_FIELD_SPECS` instead. Core
#: fields need their own table because operator availability there is a
#: property of the individual field, not of its type - a core ``title`` is
#: TEXT-typed but has no ``is_missing`` (the column is not nullable) and no
#: ``in`` (``CHAR_KWARGS`` does not include one), while a Text *custom field*
#: has both.
#:
#: Order matters: it is the order the frontend renders the operator dropdown
#: in, so the common case comes first.
CUSTOM_FIELD_OPERATORS: dict[FieldType, tuple[FilterOperator, ...]] = {
    FieldType.TEXT: (
        FilterOperator.CONTAINS,
        FilterOperator.EQUALS,
        FilterOperator.STARTS_WITH,
        FilterOperator.ENDS_WITH,
        FilterOperator.IN,
        *_MISSING_FAMILY,
        FilterOperator.IS_EMPTY,
    ),
    FieldType.LONG_TEXT: (
        FilterOperator.CONTAINS,
        FilterOperator.EQUALS,
        FilterOperator.STARTS_WITH,
        FilterOperator.ENDS_WITH,
        FilterOperator.IN,
        *_MISSING_FAMILY,
        FilterOperator.IS_EMPTY,
    ),
    FieldType.URL: (
        FilterOperator.CONTAINS,
        FilterOperator.EQUALS,
        FilterOperator.STARTS_WITH,
        FilterOperator.ENDS_WITH,
        FilterOperator.IN,
        *_MISSING_FAMILY,
        FilterOperator.IS_EMPTY,
    ),
    # No CONTAINS/STARTS_WITH here even though Paperless technically allows
    # string lookups on Monetary: those run against `value_monetary`, the raw
    # "EUR450.00" string, so `contains "0.0"` would match on the currency
    # code's neighbours and on digits of unrelated magnitude. An amount is a
    # number; comparing it as text is a trap, not a feature.
    FieldType.MONETARY: (
        FilterOperator.EQUALS,
        *_COMPARISON,
        *_MISSING_FAMILY,
    ),
    FieldType.DATE: (
        FilterOperator.EQUALS,
        *_COMPARISON,
        *_MISSING_FAMILY,
    ),
    FieldType.INTEGER: (
        FilterOperator.EQUALS,
        *_COMPARISON,
        *_MISSING_FAMILY,
    ),
    FieldType.FLOAT: (
        FilterOperator.EQUALS,
        *_COMPARISON,
        *_MISSING_FAMILY,
    ),
    FieldType.BOOLEAN: (
        FilterOperator.EQUALS,
        *_MISSING_FAMILY,
    ),
    FieldType.SELECT: (
        FilterOperator.EQUALS,
        FilterOperator.IN,
        *_MISSING_FAMILY,
    ),
    # `contains` (the document-link membership operator) is not exposed in
    # M4: its value is a list of document ids, which the Explorer has no way
    # to let a user pick yet. The missing/present family is exposed because
    # it needs no value and is genuinely useful for data-quality triage.
    FieldType.DOCUMENT_LINK: _MISSING_FAMILY,
}


_SCALAR_SHAPE: dict[FieldType, ValueShape] = {
    FieldType.TEXT: ValueShape.TEXT,
    FieldType.LONG_TEXT: ValueShape.TEXT,
    FieldType.URL: ValueShape.TEXT,
    FieldType.MONETARY: ValueShape.DECIMAL,
    FieldType.DATE: ValueShape.DATE,
    FieldType.DATETIME: ValueShape.DATE,
    FieldType.INTEGER: ValueShape.INTEGER,
    FieldType.FLOAT: ValueShape.FLOAT,
    FieldType.BOOLEAN: ValueShape.BOOLEAN,
    FieldType.SELECT: ValueShape.SELECT_OPTION,
    FieldType.DOCUMENT_LINK: ValueShape.INTEGER,
    FieldType.REFERENCE: ValueShape.REFERENCE_ID,
    FieldType.TAG_SET: ValueShape.REFERENCE_ID,
}


def value_shape(field_type: FieldType, operator: FilterOperator) -> ValueShape:
    """The shape ``value`` must have for this (field type, operator) pair.

    Returns :attr:`ValueShape.NONE` for the operators that take no value.
    List-valued operators return the *element* shape - the list-ness is
    carried by :data:`~paperwrench.filters.model.LIST_OPERATORS`, so the
    frontend renders "a multi-select of this shape".
    """
    from paperwrench.filters.model import VALUELESS_OPERATORS

    if operator in VALUELESS_OPERATORS:
        return ValueShape.NONE
    return _SCALAR_SHAPE[field_type]


#: Human-readable caveats attached to a specific (field type, operator) pair.
#:
#: These are served to the frontend and shown next to the operator, because a
#: filter engine that is subtly different from what the user assumed is worse
#: than one that is visibly limited. Each of these is a real Paperless
#: behaviour, not a PaperWrench choice.
OPERATOR_NOTES: dict[tuple[FieldType, FilterOperator], str] = {
    (FieldType.TEXT, FilterOperator.EQUALS): (
        "Case-insensitive: Paperless exposes no case-sensitive exact match "
        "for text."
    ),
    (FieldType.LONG_TEXT, FilterOperator.EQUALS): (
        "Case-insensitive: Paperless exposes no case-sensitive exact match "
        "for text."
    ),
    (FieldType.URL, FilterOperator.EQUALS): (
        "Case-insensitive: Paperless exposes no case-sensitive exact match "
        "for text."
    ),
    (FieldType.TEXT, FilterOperator.CONTAINS): "Case-insensitive substring match.",
    (FieldType.LONG_TEXT, FilterOperator.CONTAINS): "Case-insensitive substring match.",
    (FieldType.URL, FilterOperator.CONTAINS): "Case-insensitive substring match.",
    (FieldType.MONETARY, FilterOperator.EQUALS): (
        "Compared as a number, ignoring the currency code: Paperless stores "
        "the comparable amount in a generated column that strips the ISO-4217 "
        "prefix. EUR10.00 and USD10.00 are equal to this operator."
    ),
    (FieldType.MONETARY, FilterOperator.GREATER_THAN): (
        "Compared as a number, ignoring the currency code."
    ),
    (FieldType.DATETIME, FilterOperator.EQUALS): (
        "Compared at day granularity: the whole calendar day matches, not an "
        "exact timestamp."
    ),
    (FieldType.REFERENCE, FilterOperator.NOT_EQUALS): (
        "Documents with no value set also match, since they are not the "
        "excluded one."
    ),
    (FieldType.TAG_SET, FilterOperator.HAS_ALL_OF): "The document carries every listed tag.",
    (FieldType.TAG_SET, FilterOperator.HAS_ANY_OF): (
        "The document carries at least one of the listed tags."
    ),
    (FieldType.TAG_SET, FilterOperator.HAS_NONE_OF): (
        "The document carries none of the listed tags."
    ),
    (FieldType.TAG_SET, FilterOperator.IS_MISSING): "The document has no tags at all.",
    (FieldType.TAG_SET, FilterOperator.IS_PRESENT): "The document has at least one tag.",
    (FieldType.REFERENCE, FilterOperator.IS_MISSING): "No value is set on the document.",
    (FieldType.REFERENCE, FilterOperator.IS_PRESENT): "Some value is set on the document.",
}


#: Per-operator descriptions of what the empty/missing family actually means.
#: Shared by every field type, because the whole point is that these mean the
#: same thing everywhere.
OPERATOR_SEMANTICS: dict[FilterOperator, str] = {
    FilterOperator.IS_MISSING: (
        "The document carries no value for this field at all (ABSENT). For a "
        "custom field this means no field instance exists on the document - "
        "distinct from an instance whose value is null."
    ),
    FilterOperator.IS_PRESENT: (
        "The document carries a value for this field, whatever it is - "
        "including null, an empty string, 0 and false."
    ),
    FilterOperator.IS_NULL: (
        "The field instance exists on the document and its value is "
        "explicitly null. Never matches a document where the field is ABSENT."
    ),
    FilterOperator.HAS_VALUE: (
        "The field instance exists and its value is not null. Still matches "
        'an empty string, 0 and false.'
    ),
    FilterOperator.IS_EMPTY: (
        "The field instance exists and its value is either null or the empty "
        "string. Deliberately does NOT match a document where the field is "
        "ABSENT - use 'is missing' for that."
    ),
}


@dataclass(frozen=True)
class CoreFieldSpec:
    """A core document field the Filter Engine exposes.

    ``operators`` is explicit rather than derived from ``field_type``
    because, for core fields, availability is a property of the individual
    column: ``title`` is TEXT but not nullable and has no ``in`` lookup,
    while ``archive_serial_number`` is INTEGER *and* nullable. Deriving
    these would mean either offering operators Paperless silently ignores
    or hiding ones it supports.
    """

    field: CoreField
    field_type: FieldType
    label: str
    operators: tuple[FilterOperator, ...]
    #: What a value of this field references, when it is a reference. Used by
    #: the frontend to pick the right picker (tags / correspondents / ...).
    reference_kind: str | None = None


#: Operators shared by ``correspondent``, ``document_type`` and
#: ``storage_path`` - all three are nullable foreign keys with the same
#: ObjectFilter family in ``DocumentFilterSet`` (``__id``, ``__id__in``,
#: ``__id__none``) plus an ``isnull`` lookup.
_REFERENCE_OPERATORS = (
    FilterOperator.EQUALS,
    FilterOperator.NOT_EQUALS,
    FilterOperator.IN,
    FilterOperator.IS_MISSING,
    FilterOperator.IS_PRESENT,
)

#: VERIFIED_SOURCE (3.1.2): ``DATE_KWARGS``/``DATETIME_KWARGS`` expose
#: gt/gte/lt/lte but **no** ``exact``. ``EQUALS`` is still offered because it
#: compiles exactly, to a ``>= d`` **and** ``<= d`` pair on the same day -
#: see the compiler. Neither date column is nullable, so no missing family.
_DATE_OPERATORS = (
    FilterOperator.EQUALS,
    *_COMPARISON,
)


#: The MVP core field set (M4 brief §6). Small on purpose - see the module
#: docstring. `storage_path` is included because it is exactly the same
#: ObjectFilter shape as correspondent/document_type, so it costs nothing to
#: support correctly and would otherwise be a conspicuous hole.
CORE_FIELD_SPECS: dict[CoreField, CoreFieldSpec] = {
    # CHAR_KWARGS is istartswith/iendswith/icontains/iexact - there is no
    # case-sensitive exact match and no `in`, so neither is offered.
    CoreField.TITLE: CoreFieldSpec(
        CoreField.TITLE,
        FieldType.TEXT,
        "Title",
        (
            FilterOperator.CONTAINS,
            FilterOperator.EQUALS,
            FilterOperator.STARTS_WITH,
            FilterOperator.ENDS_WITH,
        ),
    ),
    CoreField.CORRESPONDENT: CoreFieldSpec(
        CoreField.CORRESPONDENT,
        FieldType.REFERENCE,
        "Correspondent",
        _REFERENCE_OPERATORS,
        "correspondent",
    ),
    CoreField.DOCUMENT_TYPE: CoreFieldSpec(
        CoreField.DOCUMENT_TYPE,
        FieldType.REFERENCE,
        "Document type",
        _REFERENCE_OPERATORS,
        "document_type",
    ),
    CoreField.STORAGE_PATH: CoreFieldSpec(
        CoreField.STORAGE_PATH,
        FieldType.REFERENCE,
        "Storage path",
        _REFERENCE_OPERATORS,
        "storage_path",
    ),
    # Tags are a set, so the operators are set operators. There is
    # deliberately no `equals`: "the document's tags are exactly {A, B}" has
    # no server-side expression, and offering an `equals` that silently meant
    # `has_all_of` would be the worst of both.
    CoreField.TAGS: CoreFieldSpec(
        CoreField.TAGS,
        FieldType.TAG_SET,
        "Tags",
        (
            FilterOperator.HAS_ALL_OF,
            FilterOperator.HAS_ANY_OF,
            FilterOperator.HAS_NONE_OF,
            FilterOperator.IS_MISSING,
            FilterOperator.IS_PRESENT,
        ),
        "tag",
    ),
    # VERIFIED_SOURCE (3.1.2, documents/models.py): `created` is a DateField,
    # while `added` and `modified` are DateTimeFields. That is not cosmetic -
    # it is why the compiler uses plain `created__gte` but `added__date__gte`,
    # and why they are two different FieldTypes here.
    CoreField.CREATED: CoreFieldSpec(
        CoreField.CREATED, FieldType.DATE, "Created", _DATE_OPERATORS
    ),
    CoreField.ADDED: CoreFieldSpec(
        CoreField.ADDED, FieldType.DATETIME, "Added", _DATE_OPERATORS
    ),
    CoreField.MODIFIED: CoreFieldSpec(
        CoreField.MODIFIED, FieldType.DATETIME, "Modified", _DATE_OPERATORS
    ),
    # INT_KWARGS includes both `exact` and `isnull`, and the column really is
    # nullable, so this is the one core field with a missing/present pair.
    CoreField.ARCHIVE_SERIAL_NUMBER: CoreFieldSpec(
        CoreField.ARCHIVE_SERIAL_NUMBER,
        FieldType.INTEGER,
        "Archive serial number",
        (
            FilterOperator.EQUALS,
            *_COMPARISON,
            FilterOperator.IS_MISSING,
            FilterOperator.IS_PRESENT,
        ),
    ),
}


CUSTOM_FIELD_TYPE_MAP: dict[CustomFieldDataType, FieldType] = {
    CustomFieldDataType.STRING: FieldType.TEXT,
    CustomFieldDataType.LONG_TEXT: FieldType.LONG_TEXT,
    CustomFieldDataType.URL: FieldType.URL,
    CustomFieldDataType.MONETARY: FieldType.MONETARY,
    CustomFieldDataType.DATE: FieldType.DATE,
    CustomFieldDataType.INTEGER: FieldType.INTEGER,
    CustomFieldDataType.FLOAT: FieldType.FLOAT,
    CustomFieldDataType.BOOLEAN: FieldType.BOOLEAN,
    CustomFieldDataType.SELECT: FieldType.SELECT,
    CustomFieldDataType.DOCUMENT_LINK: FieldType.DOCUMENT_LINK,
}


@dataclass(frozen=True)
class ResolvedField:
    """A :class:`FieldRef` resolved against the catalog.

    ``key`` is the reference's stable identity (``core:title``,
    ``custom_field:17``) and is what appears in error messages, so a
    diagnostic always names something the caller sent rather than a display
    label that may have changed.
    """

    key: str
    label: str
    field_type: FieldType
    is_custom: bool
    custom_field_id: int | None = None
    #: The core field this resolves to, when it is one. Carried explicitly so
    #: the compiler never has to re-inspect the caller's reference object.
    core_field: CoreField | None = None
    reference_kind: str | None = None
    #: Stored option ids of a select custom field. Empty for anything else.
    select_option_ids: frozenset[str] = frozenset()
    #: Exactly the operators this field accepts. Comes from
    #: :data:`CUSTOM_FIELD_OPERATORS` for a custom field and from the field's
    #: own :class:`CoreFieldSpec` for a core one.
    operators: tuple[FilterOperator, ...] = ()


class UnknownFieldError(LookupError):
    """The catalog has no field matching this reference.

    For a custom field this means the id does not exist in the Metadata
    Registry's current snapshot - because it never existed, or because it
    was deleted upstream while a saved filter still referenced it. Either
    way the answer is a hard error: silently dropping the condition would
    widen the filter, which is the failure mode ADR-0007 exists to prevent.
    """


class FieldCatalog:
    """An immutable snapshot of every filterable field.

    Built once per request from the Metadata Registry (see
    :func:`build_catalog`), then consulted by the validator and the compiler
    without any further I/O.
    """

    def __init__(self, custom_fields: list[CustomField]) -> None:
        self._custom: dict[int, ResolvedField] = {}
        for definition in custom_fields:
            field_type = CUSTOM_FIELD_TYPE_MAP.get(definition.data_type)
            if field_type is None:  # pragma: no cover - defensive
                # An upstream data type PaperWrench does not model yet. It is
                # left out of the catalog entirely rather than guessed at, so
                # a filter on it fails as "unknown field" instead of being
                # compiled with the wrong lookup.
                continue
            self._custom[definition.id] = ResolvedField(
                key=f"custom_field:{definition.id}",
                label=definition.name,
                field_type=field_type,
                is_custom=True,
                custom_field_id=definition.id,
                select_option_ids=frozenset(
                    str(option["id"])
                    for option in definition.select_options
                    if isinstance(option, dict) and option.get("id") is not None
                ),
                operators=CUSTOM_FIELD_OPERATORS[field_type],
            )

    @property
    def custom_fields(self) -> list[ResolvedField]:
        return [self._custom[key] for key in sorted(self._custom)]

    @property
    def core_fields(self) -> list[ResolvedField]:
        return [self._resolve_core(spec) for spec in CORE_FIELD_SPECS.values()]

    @staticmethod
    def _resolve_core(spec: CoreFieldSpec) -> ResolvedField:
        return ResolvedField(
            key=f"core:{spec.field.value}",
            label=spec.label,
            field_type=spec.field_type,
            is_custom=False,
            core_field=spec.field,
            reference_kind=spec.reference_kind,
            operators=spec.operators,
        )

    def resolve(self, ref: CoreFieldRef | CustomFieldRef) -> ResolvedField:
        """Resolve a field reference, or raise :class:`UnknownFieldError`."""
        if isinstance(ref, CoreFieldRef):
            spec = CORE_FIELD_SPECS.get(ref.name)
            if spec is None:  # pragma: no cover - CoreField is a closed enum
                raise UnknownFieldError(ref.key)
            return self._resolve_core(spec)
        resolved = self._custom.get(ref.field_id)
        if resolved is None:
            raise UnknownFieldError(ref.key)
        return resolved
