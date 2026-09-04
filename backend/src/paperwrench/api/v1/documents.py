"""The Explorer's documents API - M3.

This is PaperWrench's own, normalized view of "a page of documents", never a
passthrough of Paperless's ``DocumentSerializer``. The frontend (TanStack
Table) reads this shape and this shape only; it must not need to know
anything about Paperless's field names, its ``{count, next, previous,
results}`` envelope, or its ordering quirks.

Scope (M3, read-only):

* server-side pagination (``page``, ``page_size``), never a fetch-all
* simple search (``search`` -> Paperless ``title_search``, ``query`` opaque
  full-text passthrough) - no Tantivy syntax parsing here, ever
* ordering restricted to a **server-defined allowlist**, because M1 already
  proved (``docs/paperless-api.md`` §6) that Paperless silently *ignores* an
  ordering value it does not recognise instead of rejecting it. A typo here
  must fail loudly in PaperWrench, not silently stop sorting while looking
  like it worked.
* basic direct metadata filters (document type, correspondent, tag) that map
  onto Paperless params already proven to compose with pagination and
  ordering (M1). No FilterSet, no filter DSL, no nested AND/OR - that is M4.
* dynamic custom-field columns, resolved through the shared
  :class:`~paperwrench.paperless.registry.MetadataRegistry`, preserving the
  ABSENT/NULL/PRESENT distinction and Decimal-safe monetary amounts.

Nothing in this module ever issues a PATCH, POST or DELETE to Paperless.
"""

from __future__ import annotations

from typing import Annotated
from typing import Any

from fastapi import APIRouter
from fastapi import Depends
from fastapi import Query
from pydantic import BaseModel
from pydantic import Field

from paperwrench.api.deps import get_metadata_registry
from paperwrench.api.deps import get_paperless_client
from paperwrench.errors import InvalidOrderingError
from paperwrench.errors import InvalidPageSizeError
from paperwrench.paperless import Correspondent
from paperwrench.paperless import CustomField
from paperwrench.paperless import Document
from paperwrench.paperless import DocumentType
from paperwrench.paperless import MetadataRegistry
from paperwrench.paperless import PaperlessClient
from paperwrench.paperless import Tag
from paperwrench.paperless.models import CustomFieldValueKind
from paperwrench.paperless.registry import MetadataNotFoundError

router = APIRouter(prefix="/documents", tags=["documents"])

# --------------------------------------------------------------- page sizes
#: The only page sizes the Explorer may request. Deliberately finite and
#: deliberately with no "ALL" option (M3 brief): the point of a paginated
#: grid is defeated by a size that lets a caller ask for the whole library.
ALLOWED_PAGE_SIZES: tuple[int, ...] = (25, 50, 100, 250)
DEFAULT_PAGE_SIZE = 100

# ----------------------------------------------------------- core ordering
#: Core (non-custom-field) ordering values PaperWrench exposes.
#:
#: VERIFIED_SOURCE (Paperless-ngx 3.1.2, ``src/documents/views.py``,
#: ``DocumentViewSet.ordering_fields``): the server accepts ordering by
#: ``id, title, correspondent__name, document_type__name,
#: storage_path__name, created, modified, added, archive_serial_number,
#: num_notes, owner, page_count`` plus a ``custom_field_`` prefix. This is
#: the subset PaperWrench actually surfaces as document-list columns; the
#: rest (``num_notes``, ``owner``, ``page_count``, ``storage_path__name``)
#: is not yet a grid column and is intentionally left out until something
#: needs it, per the M3 brief's instruction not to expose fields "just
#: because the server accepts them".
#:
#: The dict value is the exact Paperless ``ordering`` parameter value for
#: ascending order; descending is the same value prefixed with ``-``
#: (VERIFIED_SOURCE: standard DRF ``OrderingFilter`` convention, used
#: identically by ``DocumentsOrderingFilter``).
CORE_ORDERING_FIELDS: dict[str, str] = {
    "title": "title",
    "created": "created",
    "modified": "modified",
    "added": "added",
    "archive_serial_number": "archive_serial_number",
    "correspondent": "correspondent__name",
    "document_type": "document_type__name",
}

#: Prefix for a custom-field ordering key as PaperWrench exposes it, e.g.
#: ``ordering=custom_field_42``. Mirrors Paperless's own convention
#: (VERIFIED_SOURCE), so the value can be passed straight through once
#: validated.
CUSTOM_FIELD_ORDERING_PREFIX = "custom_field_"

#: Custom field data types for which ``custom_field_<id>`` ordering is
#: exposed in M3.
#:
#: VERIFIED_SOURCE (``src/documents/filters.py``,
#: ``DocumentsOrderingFilter``): Paperless-ngx 3.1.2 implements
#: ``custom_field_<id>`` ordering for STRING, LONG_TEXT, INT, FLOAT, DATE,
#: MONETARY, SELECT, DOCUMENTLINK, URL and BOOL alike, each via its own
#: per-type value column, always ordering documents that HAVE the field
#: before documents that do not (``-has_field`` first, regardless of
#: direction on the value itself).
#:
#: The M3 brief only asked for Text, Monetary and Date to be *exposed* in
#: the UI - not for every type the server happens to support - and
#: explicitly requires this to be confirmed against a real 3.1.2 instance
#: before being relied upon (see ``tests/backend/live``). Until that live
#: run has actually executed, this is VERIFIED_SOURCE only for the ordering
#: *implementation*, not VERIFIED_LIVE for PaperWrench's *exposure* of it -
#: see docs/paperless-api.md for the promoted status.
SORTABLE_CUSTOM_FIELD_DATA_TYPES = frozenset({"string", "longtext", "monetary", "date"})


def resolve_ordering(
    ordering: str | None, *, sortable_custom_field_ids: frozenset[int]
) -> str | None:
    """Translate a PaperWrench ordering key into the Paperless ``ordering`` value.

    Returns ``None`` when ``ordering`` is ``None`` (no explicit sort - the
    caller then omits the parameter entirely and gets Paperless's default).
    Raises :class:`InvalidOrderingError` for anything not on the allowlist,
    which is the whole point: an unknown value must fail loudly in
    PaperWrench rather than being silently ignored by Paperless (M1
    finding).

    ``sortable_custom_field_ids`` must already be filtered down to the ids
    whose ``data_type`` is one of :data:`SORTABLE_CUSTOM_FIELD_DATA_TYPES` -
    see :func:`_sortable_custom_field_ids`. A custom field that exists but
    has an unexposed data type (e.g. SELECT, BOOLEAN) is rejected exactly
    like an unknown id: M3 only exposes ordering for Text/Monetary/Date.
    """
    if ordering is None:
        return None

    descending = ordering.startswith("-")
    key = ordering[1:] if descending else ordering

    if key in CORE_ORDERING_FIELDS:
        target = CORE_ORDERING_FIELDS[key]
        return f"-{target}" if descending else target

    if key.startswith(CUSTOM_FIELD_ORDERING_PREFIX):
        raw_id = key[len(CUSTOM_FIELD_ORDERING_PREFIX) :]
        if raw_id.isdigit() and int(raw_id) in sortable_custom_field_ids:
            return f"-{key}" if descending else key

    raise InvalidOrderingError(ordering)


def _sortable_custom_field_ids(fields: list[CustomField]) -> frozenset[int]:
    """Ids of custom fields whose data type M3 exposes for ordering.

    See :data:`SORTABLE_CUSTOM_FIELD_DATA_TYPES` - only Text/Long text,
    Monetary and Date are exposed in M3, regardless of what Paperless
    itself is able to sort on (VERIFIED_SOURCE: it supports more types than
    that, see ``DocumentsOrderingFilter``).
    """
    return frozenset(
        field.id for field in fields if field.data_type.value in SORTABLE_CUSTOM_FIELD_DATA_TYPES
    )


def validate_page_size(page_size: int) -> int:
    if page_size not in ALLOWED_PAGE_SIZES:
        raise InvalidPageSizeError(page_size, ALLOWED_PAGE_SIZES)
    return page_size


# ------------------------------------------------------------------- DTOs
class MetadataRef(BaseModel):
    """A resolved reference to a tag/correspondent/document type/storage path.

    ``name`` is ``None`` when the id could not be resolved against the
    Metadata Registry (the object was deleted upstream after this document
    last referenced it, a stale cache window, ...). The frontend renders
    that as ``Unknown (#<id>)`` - see the M3 brief - rather than crashing or
    silently coercing the reference to ``null``, which would make a real
    reference indistinguishable from "no correspondent set".
    """

    id: int
    name: str | None = None


class MonetaryValueDto(BaseModel):
    """A monetary custom field value, Decimal-safe end to end.

    ``amount`` is a ``Decimal`` serialised as a JSON string (never a JS
    float), matching :class:`~paperwrench.paperless.models.MonetaryAmount`.
    Formatting for display (thousands separators, symbol placement) is a
    frontend concern; this DTO carries the exact value only.
    """

    currency: str
    amount: Any  # Decimal; typed loosely here so response_model round-trips as a string


class CustomFieldColumnValue(BaseModel):
    """The typed value of one custom field on one document row.

    Mirrors :class:`~paperwrench.paperless.models.TypedCustomFieldValue`
    exactly on purpose: ABSENT / NULL / PRESENT are never collapsed, ``raw``
    is always the untouched source value, and ``monetary``/``select_*`` are
    derived conveniences layered on top of it, never a replacement for it.
    """

    field_id: int
    kind: CustomFieldValueKind
    raw: Any = None
    monetary: MonetaryValueDto | None = None
    select_option_id: str | None = None
    select_label: str | None = None


class DocumentListItem(BaseModel):
    """One row of the Explorer grid.

    Deliberately not a copy of Paperless's ``DocumentSerializer``: every
    reference field is already resolved to a display name (or explicitly
    unresolved, see :class:`MetadataRef`), every custom field is already
    typed, and ``tags`` carries enough to render a badge without a further
    round trip.
    """

    id: int
    title: str
    correspondent: MetadataRef | None = None
    document_type: MetadataRef | None = None
    tags: list[MetadataRef] = Field(default_factory=list)
    created: str | None = None
    modified: str | None = None
    added: str | None = None
    archive_serial_number: int | None = None
    custom_fields: list[CustomFieldColumnValue] = Field(default_factory=list)
    #: Preserved through from Paperless (VERIFIED_LIVE, M2) for later
    #: milestones (Inspector edit-affordance, bulk-operation pre-flight).
    #: Not given special UI treatment in M3 itself.
    user_can_change: bool | None = None


class DocumentPage(BaseModel):
    """The paginated envelope the Explorer actually consumes.

    Deliberately NOT Paperless's ``{count, next, previous, results}`` -
    ``next``/``previous`` are absolute URLs built from Paperless's own
    notion of its hostname (unusable behind a reverse proxy, per M1), and a
    page-number-based UI needs a page count, not a "is there a next" flag.
    """

    items: list[DocumentListItem]
    page: int
    page_size: int
    total: int
    page_count: int


# ------------------------------------------------------------------ mapping
async def _resolve_ref(
    registry: MetadataRegistry, kind: str, object_id: int | None
) -> MetadataRef | None:
    if object_id is None:
        return None
    obj: Tag | Correspondent | DocumentType
    try:
        if kind == "tag":
            obj = await registry.tag_by_id(object_id)
        elif kind == "correspondent":
            obj = await registry.correspondent_by_id(object_id)
        elif kind == "document_type":
            obj = await registry.document_type_by_id(object_id)
        else:  # pragma: no cover - defensive, kind is internal
            raise ValueError(f"unknown metadata kind {kind!r}")
    except MetadataNotFoundError:
        # A reference to something the registry does not (or no longer)
        # know about must render as "Unknown (#id)", never crash and never
        # silently become a null reference (M3 brief) - see MetadataRef.
        return MetadataRef(id=object_id, name=None)
    return MetadataRef(id=obj.id, name=obj.name)


async def _document_to_list_item(
    document: Document, registry: MetadataRegistry
) -> DocumentListItem:
    correspondent = await _resolve_ref(registry, "correspondent", document.correspondent)
    document_type = await _resolve_ref(registry, "document_type", document.document_type)
    tags = [ref for ref in await _resolve_tags(registry, document.tags) if ref is not None]

    definitions = {field.id: field for field in await registry.all_custom_fields()}
    typed = document.typed_custom_fields(definitions)
    custom_fields = [
        CustomFieldColumnValue(
            field_id=value.field_id,
            kind=value.kind,
            raw=value.raw,
            monetary=(
                MonetaryValueDto(currency=value.monetary.currency, amount=value.monetary.amount)
                if value.monetary is not None
                else None
            ),
            select_option_id=value.select_option_id,
            select_label=value.select_label,
        )
        for value in typed.values()
    ]

    return DocumentListItem(
        id=document.id,
        title=document.title,
        correspondent=correspondent,
        document_type=document_type,
        tags=tags,
        created=document.created,
        modified=document.modified,
        added=document.added,
        archive_serial_number=document.archive_serial_number,
        custom_fields=custom_fields,
        user_can_change=document.user_can_change,
    )


async def _resolve_tags(
    registry: MetadataRegistry, tag_ids: list[int]
) -> list[MetadataRef | None]:
    return [await _resolve_ref(registry, "tag", tag_id) for tag_id in tag_ids]


# ------------------------------------------------------------------- route
@router.get(
    "",
    response_model=DocumentPage,
    summary="List documents (server-side paginated, sorted and searched)",
)
async def list_documents(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query()] = DEFAULT_PAGE_SIZE,
    search: Annotated[
        str | None,
        Query(
            description=(
                "Simple title search. Mapped to Paperless `title_search` - an "
                "opaque string, never parsed as Tantivy syntax here."
            )
        ),
    ] = None,
    query: Annotated[
        str | None,
        Query(
            description=(
                "Optional advanced full-text search, mapped to Paperless's "
                "`query` parameter as-is. Distinct from `search`: Paperless "
                "itself rejects specifying more than one search mode at once."
            )
        ),
    ] = None,
    ordering: Annotated[
        str | None,
        Query(
            description=(
                "One of the allowlisted ordering keys (optionally `-`-prefixed "
                "for descending). An unknown value is rejected with 422 - it is "
                "never forwarded to Paperless, which would otherwise silently "
                "ignore it."
            )
        ),
    ] = None,
    document_type: Annotated[int | None, Query(description="Filter by document type id")] = None,
    correspondent: Annotated[int | None, Query(description="Filter by correspondent id")] = None,
    tag: Annotated[int | None, Query(description="Filter by a single tag id")] = None,
    client: PaperlessClient = Depends(get_paperless_client),
    registry: MetadataRegistry = Depends(get_metadata_registry),
) -> DocumentPage:
    validate_page_size(page_size)

    all_fields = await registry.all_custom_fields()
    sortable_custom_field_ids = _sortable_custom_field_ids(all_fields)
    paperless_ordering = resolve_ordering(
        ordering, sortable_custom_field_ids=sortable_custom_field_ids
    )

    if search is not None and query is not None:
        # Mirrors Paperless's own rule (VERIFIED_SOURCE,
        # `_TANTIVY_SEARCH_PARAM_NAMES` validation in `DocumentViewSet.list`):
        # specifying more than one search mode is a client error, not
        # something to silently pick one of. Caught here so the message is
        # PaperWrench's own, not an upstream 400 body.
        from paperwrench.errors import ErrorCode
        from paperwrench.errors import PaperWrenchError

        raise PaperWrenchError(
            "Specify only one of `search` or `query`, not both.",
            status_code=422,
            code=ErrorCode.VALIDATION_ERROR,
        )

    params: dict[str, Any] = {}
    if search is not None:
        params["title_search"] = search
    if query is not None:
        params["query"] = query
    if paperless_ordering is not None:
        params["ordering"] = paperless_ordering
    if document_type is not None:
        params["document_type__id"] = document_type
    if correspondent is not None:
        params["correspondent__id"] = correspondent
    if tag is not None:
        params["tags__id__all"] = tag

    paperless_page = await client.list_documents(params=params, page=page, page_size=page_size)

    items = [
        await _document_to_list_item(document, registry) for document in paperless_page.results
    ]

    total = paperless_page.count
    page_count = (total + page_size - 1) // page_size if page_size else 0

    return DocumentPage(
        items=items,
        page=page,
        page_size=page_size,
        total=total,
        page_count=page_count,
    )
