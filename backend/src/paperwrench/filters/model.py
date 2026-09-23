"""The FilterSet domain model - PaperWrench's own query language.

This module is deliberately **independent of Paperless HTTP syntax**. Nothing
here knows about ``custom_field_query``, ``tags__id__all`` or DRF lookup
suffixes; that translation lives entirely in :mod:`paperwrench.filters.compiler`
and is allowed to refuse. The separation is the point:

* the *domain model* describes what a user meant;
* the *compiler* decides whether Paperless can be asked that question exactly.

A FilterSet that this module accepts is therefore **not** automatically a
FilterSet the compiler can translate (ADR-0007). "Structurally valid" and
"compilable" are two different verdicts, reported separately by
``POST /api/v1/filters/validate``.

Why this matters more than it looks: a FilterSet is eventually the definition
of a transformation's blast radius (M6+). Being approximately right is not an
option, so every part of the model is built to *fail loudly* rather than
degrade:

* a custom field is identified by its **stable Paperless id**, never its name
  (a rename must not silently change what a saved filter means);
* a select option is identified by its **stored option id**, never its label
  (same reason);
* a monetary value is carried as a **string** and parsed to ``Decimal`` -
  a JSON float is rejected outright, because ``0.1 + 0.2`` is not a rounding
  detail when the number decides which invoices get rewritten.

Search is **not** part of this model - see :class:`SearchSpec` and ADR-0010.
Ordering and pagination are not either; a dataset is
``SearchSpec + FilterSet + Ordering``, and pagination is only a view of it
(:class:`DatasetQuery`).
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Annotated
from typing import Any
from typing import Literal
from typing import Union

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

# --------------------------------------------------------------- field refs


class CoreField(StrEnum):
    """The core (non-custom) document fields the Filter Engine exposes.

    Deliberately a closed enum rather than a free string: an unknown core
    field must be a *validation* failure at the edge of the API, not
    something that reaches the compiler and gets a chance to be forwarded.
    M1 proved Paperless silently ignores an unknown filter parameter
    (``docs/paperless-api.md`` §6), so "unknown field" is a data-loss risk,
    not a typo.

    This is a subset of what Paperless's ``DocumentFilterSet`` accepts. It
    is small on purpose: a field is added here only once its operators have
    a translation that has actually been read in the 3.1.2 source and
    exercised live.
    """

    TITLE = "title"
    CORRESPONDENT = "correspondent"
    DOCUMENT_TYPE = "document_type"
    STORAGE_PATH = "storage_path"
    TAGS = "tags"
    CREATED = "created"
    ADDED = "added"
    MODIFIED = "modified"
    ARCHIVE_SERIAL_NUMBER = "archive_serial_number"


class FieldSource(StrEnum):
    """Where a :class:`FieldRef` gets its identity from."""

    CORE = "core"
    CUSTOM_FIELD = "custom_field"


class CoreFieldRef(BaseModel):
    """A reference to a fixed field of the Paperless document schema."""

    model_config = ConfigDict(extra="forbid")

    source: Literal[FieldSource.CORE] = FieldSource.CORE
    name: CoreField

    @property
    def key(self) -> str:
        """Stable, human-readable identity, e.g. ``core:title``."""
        return f"core:{self.name.value}"


class CustomFieldRef(BaseModel):
    """A reference to a user-defined Paperless custom field, **by id**.

    ``field_id`` is the identity and the only thing that is ever compiled or
    persisted. ``display_name`` exists so a stored FilterSet can be rendered
    without a metadata round-trip, and so a diagnostic can say *which* field
    disappeared - it is **never** matched against, never sent to Paperless,
    and never used to resolve the field. Renaming a custom field in Paperless
    must not change the meaning of a saved filter.

    (Paperless's own ``custom_field_query`` does accept a name in place of an
    id. PaperWrench does not use that affordance: the M2 Metadata Registry
    already established that names are not a safe key - two objects owned by
    different users may share one - and a name-keyed filter would silently
    retarget on a rename.)
    """

    model_config = ConfigDict(extra="forbid")

    source: Literal[FieldSource.CUSTOM_FIELD] = FieldSource.CUSTOM_FIELD
    field_id: int
    display_name: str | None = None

    @property
    def key(self) -> str:
        """Stable, human-readable identity, e.g. ``custom_field:17``."""
        return f"custom_field:{self.field_id}"


FieldRef = Annotated[
    Union[CoreFieldRef, CustomFieldRef],  # noqa: UP007 - pydantic discriminated union
    Field(discriminator="source"),
]


# ---------------------------------------------------------------- operators


class FilterOperator(StrEnum):
    """Every operator the domain model can express.

    Which of these are *legal* for a given field is decided by the field's
    type (see :mod:`paperwrench.filters.catalog`), and which are
    *compilable* is decided by :mod:`paperwrench.filters.compiler`. An
    operator existing here is not a promise that any field supports it.

    The empty/missing family is deliberately more than one operator, because
    M1/M2 established that ABSENT, NULL, ``""``, ``0`` and ``false`` are five
    genuinely different states and a single "is empty" would conflate them:

    ``IS_MISSING``
        The document carries no instance of this field at all (ABSENT).
    ``IS_PRESENT``
        The document carries an instance, whatever its value - including
        NULL, ``""``, ``0`` and ``false``.
    ``IS_NULL``
        The document carries an instance whose value is explicitly NULL.
        This is **not** ABSENT, and never matches an ABSENT document.
    ``HAS_VALUE``
        The document carries an instance with a non-NULL value. Still
        matches ``""``, ``0`` and ``false``.
    ``IS_EMPTY``
        NULL **or** the empty string. Text-shaped fields only. Deliberately
        does *not* include ABSENT - see ADR-0011.
    """

    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    IN = "in"
    CONTAINS = "contains"
    STARTS_WITH = "starts_with"
    ENDS_WITH = "ends_with"
    GREATER_THAN = "greater_than"
    GREATER_OR_EQUAL = "greater_or_equal"
    LESS_THAN = "less_than"
    LESS_OR_EQUAL = "less_or_equal"
    HAS_ALL_OF = "has_all_of"
    HAS_ANY_OF = "has_any_of"
    HAS_NONE_OF = "has_none_of"
    IS_MISSING = "is_missing"
    IS_PRESENT = "is_present"
    IS_NULL = "is_null"
    HAS_VALUE = "has_value"
    IS_EMPTY = "is_empty"


#: Operators that take no value at all. A value supplied alongside one of
#: these is a validation error rather than something to quietly ignore: a
#: caller who sent one probably believes it does something.
VALUELESS_OPERATORS = frozenset(
    {
        FilterOperator.IS_MISSING,
        FilterOperator.IS_PRESENT,
        FilterOperator.IS_NULL,
        FilterOperator.HAS_VALUE,
        FilterOperator.IS_EMPTY,
    }
)

#: Operators whose value is a list. An empty list is always invalid: it would
#: either mean "match nothing" or "match everything" depending on the
#: operator, and guessing which is exactly the class of silent mistake this
#: engine exists to prevent.
LIST_OPERATORS = frozenset(
    {
        FilterOperator.IN,
        FilterOperator.HAS_ALL_OF,
        FilterOperator.HAS_ANY_OF,
        FilterOperator.HAS_NONE_OF,
    }
)


class GroupOperator(StrEnum):
    AND = "and"
    OR = "or"


# --------------------------------------------------------------- the tree


class FilterCondition(BaseModel):
    """One leaf: ``field <operator> value``.

    ``value`` is intentionally untyped here. Type checking is done against
    the *field's* data type by :mod:`paperwrench.filters.validation`, which
    is the only place that knows a custom field is Monetary rather than
    Text. Encoding that in the pydantic model would require a different
    model class per data type and would still not cover custom fields,
    whose type is only known at runtime from the Metadata Registry.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["condition"] = "condition"
    field: FieldRef
    operator: FilterOperator
    value: Any = None


class FilterGroup(BaseModel):
    """A boolean combination of children.

    An empty ``children`` list is only meaningful at the root, where it means
    "no filter at all" (every document matches). A nested empty group is a
    validation error - it has no defensible meaning and is almost always a UI
    bug.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["group"] = "group"
    operator: GroupOperator = GroupOperator.AND
    children: list[FilterNode] = Field(default_factory=list)


class FilterNot(BaseModel):
    """Logical negation of a subtree.

    Representable in the domain model, **not compilable in M4** - see
    ADR-0011 and the compiler's ``NOT`` handling. It exists here so the model
    does not have to change shape when negation is eventually supported, and
    so a frontend that stores one gets an explicit, structured refusal rather
    than a parse failure.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["not"] = "not"
    child: FilterNode


FilterNode = Annotated[
    Union[FilterCondition, FilterGroup, FilterNot],  # noqa: UP007
    Field(discriminator="kind"),
]


class FilterSet(BaseModel):
    """The whole filter expression.

    Always rooted in a group so that "add another condition" never has to
    change the shape of the tree, and so an empty filter (``root.children ==
    []``) is representable without a ``None``.
    """

    model_config = ConfigDict(extra="forbid")

    root: FilterGroup = Field(default_factory=FilterGroup)

    @property
    def is_empty(self) -> bool:
        return not self.root.children


FilterGroup.model_rebuild()
FilterNot.model_rebuild()
FilterSet.model_rebuild()


# ------------------------------------------------------------------ search


class SearchMode(StrEnum):
    """Which Paperless search mode a :class:`SearchSpec` selects.

    These are **three different things**, and M3's single ``search``
    parameter blurred them (it always meant TITLE). Naming them explicitly
    is the point of this enum:

    ``TITLE``
        Paperless ``title_search``. Full-text index, **restricted to the
        title field**. This is what M3's ``search`` parameter actually did.
    ``CONTENT``
        Paperless ``text``. Full-text index over the document's extracted
        text.
    ``ADVANCED``
        Paperless ``query``. The raw Tantivy query language, passed through
        **opaquely** - PaperWrench does not parse, rewrite or validate it,
        and must never pretend to.

    VERIFIED_SOURCE (3.1.2, ``documents/views.py`` ``_TANTIVY_SEARCH_PARAM_
    NAMES``): the server accepts exactly ``text``, ``title_search``,
    ``query`` and ``more_like_id``, and rejects a request that specifies
    more than one of them. ``more_like_id`` is not a text search and is not
    modelled here.
    """

    TITLE = "title"
    CONTENT = "content"
    ADVANCED = "advanced"


class SearchSpec(BaseModel):
    """A full-text search, kept deliberately *outside* the FilterSet.

    See ADR-0010. In short: Paperless evaluates these against a Tantivy
    index and intersects the resulting document ids with the ORM queryset
    (VERIFIED_SOURCE, ``DocumentViewSet.list``). It is a fundamentally
    different mechanism from a field lookup, it is limited to one mode per
    request, and ``ADVANCED`` carries a query language PaperWrench does not
    own. Forcing it into ``FilterCondition`` would mean inventing a
    pseudo-operator whose value is an opaque foreign syntax - an abstraction
    that would make the model look uniform while making it less truthful.
    """

    model_config = ConfigDict(extra="forbid")

    mode: SearchMode = SearchMode.TITLE
    text: str


# ----------------------------------------------------------------- dataset


class DatasetQuery(BaseModel):
    """The identity of a set of documents: search + filters + ordering.

    Pagination is **not** part of this. A page is a *view* of a dataset, not
    a different dataset - which is why :class:`DatasetPageRequest` composes
    this rather than extending it with page fields that would end up in the
    fingerprint.

    Nothing in M4 persists a dataset. This type exists so that the API shape
    M4 ships can already express one, because Transform (M6), Dry Run (M7),
    Jobs (M8) and Collections all need to name "the documents this operation
    is about" and would otherwise each invent their own encoding.
    """

    model_config = ConfigDict(extra="forbid")

    search: SearchSpec | None = None
    filters: FilterSet | None = None
    ordering: str | None = None

    def fingerprint(self) -> str:
        """A stable hash of this dataset's identity.

        Two ``DatasetQuery`` values that mean the same thing produce the same
        fingerprint (JSON is emitted with sorted keys), and any change to the
        search, the filter tree or the ordering produces a different one.
        Not used to make decisions in M4; it is here so that a later
        milestone comparing "the dataset the preview ran on" against "the
        dataset the job is executing" has one obvious, already-tested way to
        do it.
        """
        payload = json.dumps(
            self.model_dump(mode="json", exclude_none=True),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class DatasetPageRequest(BaseModel):
    """One page of a dataset. The Explorer's request body.

    ``page``/``page_size`` describe the window, never the dataset - see
    :class:`DatasetQuery`.
    """

    model_config = ConfigDict(extra="forbid")

    search: SearchSpec | None = None
    filters: FilterSet | None = None
    ordering: str | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = 100

    def dataset(self) -> DatasetQuery:
        return DatasetQuery(search=self.search, filters=self.filters, ordering=self.ordering)
