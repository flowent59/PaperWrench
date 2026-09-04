"""Typed views over the Paperless-ngx payloads PaperWrench actually reads.

These models are deliberately *partial*. Paperless documents carry 26 keys in
3.1.2; modelling all of them would turn every upstream addition into a
breaking change here. ``model_config = ConfigDict(extra="ignore")`` means we
describe what we use and tolerate the rest.

Everything in this file was shaped by observation against a real 3.1.2
instance, not by reading the OpenAPI schema.
"""

from __future__ import annotations

import re
from decimal import Decimal
from decimal import InvalidOperation
from enum import StrEnum
from typing import Any
from typing import Generic
from typing import TypeVar

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import field_validator

T = TypeVar("T")

# Matches "EUR450.00", "USD1234.56" - a currency code (letters) directly
# followed by a signed decimal amount with exactly two digits, no comma.
# Module-level (not a class attribute) because pydantic v2 treats an
# underscore-prefixed class attribute as a private model attribute, not a
# plain class constant, and re-wraps it in a way that breaks direct use.
_MONETARY_PATTERN = re.compile(r"^(?P<currency>[A-Za-z]{1,8})(?P<amount>-?\d+\.\d{2})$")


class CustomFieldDataType(StrEnum):
    """Custom field data types.

    VERIFIED_LIVE (3.1.2) for string, monetary, boolean, date and select.
    VERIFIED_SOURCE for the remainder (present in the 3.1.2 serialisers, not
    exercised during M1).
    """

    STRING = "string"
    URL = "url"
    DATE = "date"
    BOOLEAN = "boolean"
    INTEGER = "integer"
    FLOAT = "float"
    MONETARY = "monetary"
    DOCUMENT_LINK = "documentlink"
    SELECT = "select"
    LONG_TEXT = "longtext"


class MonetaryAmount(BaseModel):
    """A monetary custom field value, kept away from ``float`` on purpose.

    VERIFIED_LIVE (3.1.2): Paperless serialises a monetary value as a
    string such as ``"EUR450.00"`` - a currency code immediately followed
    by a two-decimal amount, dot decimal only (comma decimals are rejected
    with 400, atomically, per document; see ``paperless-api.md``). Storing
    this as ``float`` would introduce rounding error PaperWrench did not
    put there; ``Decimal`` (via a string) does not.

    ``currency`` is kept as the raw string Paperless used (there is no
    normalisation here - PaperWrench does not decide that "EUR" and "eur"
    are the same currency; it reports what was read).
    """

    model_config = ConfigDict(extra="ignore")

    currency: str
    amount: Decimal

    @classmethod
    def parse(cls, raw: str) -> MonetaryAmount:
        """Parse a Paperless monetary string. Raises ``ValueError`` if malformed.

        Deliberately strict: this mirrors the shape Paperless itself
        accepts (VERIFIED_LIVE), so a string this rejects is a string
        Paperless would also have rejected on write.
        """
        match = _MONETARY_PATTERN.match(raw)
        if not match:
            raise ValueError(f"not a recognised monetary value: {raw!r}")
        try:
            amount = Decimal(match.group("amount"))
        except InvalidOperation as exc:  # pragma: no cover - regex already guards this
            raise ValueError(f"not a recognised monetary value: {raw!r}") from exc
        return cls(currency=match.group("currency"), amount=amount)

    def __str__(self) -> str:
        """Render back in the exact shape Paperless expects on write."""
        return f"{self.currency}{self.amount:.2f}"


class CustomFieldValueKind(StrEnum):
    """Which of the four genuinely distinct states a field value is in.

    ``PRESENT`` still covers ``""``, ``0`` and ``False`` - those are real,
    non-empty-in-the-collapsing-sense values; :class:`TypedCustomFieldValue`
    exposes them through ``raw`` unchanged. Only the field being entirely
    missing from the document (``ABSENT``) or explicitly ``null``
    (``NULL``) get their own kind.
    """

    ABSENT = "absent"
    NULL = "null"
    PRESENT = "present"


class TypedCustomFieldValue(BaseModel):
    """The typed, boundary-safe view of one custom field value on one document.

    Built by :meth:`CustomField.typed_value`. Never collapses ``0`` /
    ``False`` / ``""`` / ``null`` / absent into a single "empty" idea - each
    is a distinct, inspectable ``kind`` here, which is the guarantee the
    future Quality milestone (M11) is built on.

    ``raw`` is always the untouched value Paperless returned (or ``None``
    for NULL/ABSENT) - it is the source of truth. ``monetary`` and
    ``select_option_id``/``select_label`` are *derived*, read-only
    conveniences layered on top, never a replacement for ``raw``.
    """

    model_config = ConfigDict(extra="ignore")

    field_id: int
    data_type: CustomFieldDataType
    kind: CustomFieldValueKind
    raw: Any = None

    #: Populated only when ``data_type is MONETARY`` and ``raw`` parsed
    #: cleanly. A malformed monetary string leaves this ``None`` while
    #: ``raw`` still holds exactly what Paperless sent - see
    #: :meth:`CustomField.typed_value`.
    monetary: MonetaryAmount | None = None

    #: Populated only when ``data_type is SELECT``. ``select_option_id`` is
    #: the opaque id Paperless actually stores (the only thing that may ever
    #: be written back); ``select_label`` is the resolved display label,
    #: which is ``None`` if the option id no longer exists on the field
    #: definition. The two are never conflated: a caller wanting to write
    #: must always use ``select_option_id``.
    select_option_id: str | None = None
    select_label: str | None = None

    @property
    def is_absent(self) -> bool:
        return self.kind is CustomFieldValueKind.ABSENT

    @property
    def is_null(self) -> bool:
        return self.kind is CustomFieldValueKind.NULL

    @property
    def is_present(self) -> bool:
        return self.kind is CustomFieldValueKind.PRESENT


class CustomField(BaseModel):
    """A custom field *definition* (``/api/custom_fields/``)."""

    model_config = ConfigDict(extra="ignore")

    id: int
    name: str
    data_type: CustomFieldDataType
    extra_data: dict[str, Any] = Field(default_factory=dict)

    @field_validator("extra_data", mode="before")
    @classmethod
    def _null_extra_data_means_no_extra_data(cls, value: Any) -> Any:
        """VERIFIED_LIVE (3.1.2): non-select/monetary fields return ``null``.

        Discovered running the metadata endpoints against the real sandbox:
        every string/date/boolean custom field in the Golden Dataset serves
        ``"extra_data": null`` (not an absent key, and not ``{}``). Without
        this, ``CustomField.model_validate`` raised a 500 on
        ``GET /api/v1/metadata/custom-fields`` for exactly those fields -
        only ``select`` and ``monetary`` fields (which carry real options or
        a default currency) happened to have a non-null value, so the mocked
        tests, which always supplied a dict, never caught it. ``null`` is
        normalised to ``{}`` here so the rest of the model (``select_options``,
        ``typed_value``) can keep assuming a dict.
        """
        return {} if value is None else value

    @property
    def select_options(self) -> list[dict[str, Any]]:
        """Options of a select field.

        VERIFIED_LIVE (3.1.2): options are objects with a server-generated
        opaque string ``id`` and a ``label``, e.g.
        ``{"id": "gsbRSetmXYcC2nKx", "label": "Urgent"}``. Writing a *label*
        into a document is rejected with 400 - only the id is accepted.
        """
        options = self.extra_data.get("select_options", [])
        return options if isinstance(options, list) else []

    def option_label(self, option_id: str) -> str | None:
        """Resolve a stored select option id to its display label.

        Returns ``None`` if ``option_id`` is not (or no longer) one of this
        field's options. This is a read-only convenience for *display*: it
        must never be used to replace the stored id anywhere a write is
        being prepared (see :func:`merge_custom_fields` and ADR-0004 -
        Paperless rejects a label written where it expects an id).
        """
        for option in self.select_options:
            if option.get("id") == option_id:
                label = option.get("label")
                return str(label) if label is not None else None
        return None

    def typed_value(self, entry: CustomFieldValue | None) -> TypedCustomFieldValue:
        """Build the typed view of one value of this field on a document.

        This is the boundary that keeps ``absent`` / ``null`` / ``""`` / ``0``
        / ``False`` genuinely distinct instead of collapsing them into one
        "empty" notion - a distinction the future Quality milestone (M11)
        depends on, and that a naive ``if not value`` anywhere would erase.

        ``entry`` is ``None`` when the field does not appear at all in the
        document's ``custom_fields`` list (ABSENT). A present entry whose
        ``value`` is ``None`` is NULL, not ABSENT: Paperless round-trips
        those as two different states (VERIFIED_LIVE, M1).
        """
        if entry is None:
            return TypedCustomFieldValue(
                field_id=self.id, data_type=self.data_type, kind=CustomFieldValueKind.ABSENT
            )
        if entry.value is None:
            return TypedCustomFieldValue(
                field_id=self.id, data_type=self.data_type, kind=CustomFieldValueKind.NULL
            )

        raw = entry.value
        monetary: MonetaryAmount | None = None
        select_option_id: str | None = None
        select_label: str | None = None

        if self.data_type is CustomFieldDataType.MONETARY and isinstance(raw, str):
            # A malformed monetary string is not this method's problem to
            # solve or hide: `monetary` simply stays None and `raw` is still
            # the untouched source of truth. Never fabricate a number.
            try:
                monetary = MonetaryAmount.parse(raw)
            except ValueError:
                monetary = None
        elif self.data_type is CustomFieldDataType.SELECT and isinstance(raw, str):
            select_option_id = raw
            select_label = self.option_label(raw)

        return TypedCustomFieldValue(
            field_id=self.id,
            data_type=self.data_type,
            kind=CustomFieldValueKind.PRESENT,
            raw=raw,
            monetary=monetary,
            select_option_id=select_option_id,
            select_label=select_label,
        )


class CustomFieldValue(BaseModel):
    """A custom field *instance* on a document.

    VERIFIED_LIVE (3.1.2): serialised as ``{"field": <int id>, "value": ...}``.
    ``value`` is polymorphic: ``str`` for string/monetary/date/select,
    ``bool`` for boolean, ``None`` for an explicitly null value.
    """

    model_config = ConfigDict(extra="ignore")

    field: int
    value: Any = None


class Tag(BaseModel):
    """A tag definition (``/api/tags/``).

    VERIFIED_LIVE (3.1.2) full key set on a freshly created tag::

        id, slug, name, color, text_color, match, matching_algorithm,
        is_insensitive, is_inbox_tag, owner, user_can_change, parent,
        children
    """

    model_config = ConfigDict(extra="ignore")

    id: int
    name: str
    slug: str | None = None
    color: str | None = None
    is_inbox_tag: bool | None = None
    owner: int | None = None
    user_can_change: bool | None = None
    document_count: int | None = None


class Correspondent(BaseModel):
    """A correspondent definition (``/api/correspondents/``).

    VERIFIED_LIVE (3.1.2)::

        id, slug, name, match, matching_algorithm, is_insensitive, owner,
        user_can_change
    """

    model_config = ConfigDict(extra="ignore")

    id: int
    name: str
    slug: str | None = None
    owner: int | None = None
    user_can_change: bool | None = None
    document_count: int | None = None


class DocumentType(BaseModel):
    """A document type definition (``/api/document_types/``).

    VERIFIED_LIVE (3.1.2)::

        id, slug, name, match, matching_algorithm, is_insensitive,
        document_count, owner, user_can_change
    """

    model_config = ConfigDict(extra="ignore")

    id: int
    name: str
    slug: str | None = None
    owner: int | None = None
    user_can_change: bool | None = None
    document_count: int | None = None


class StoragePath(BaseModel):
    """A storage path definition (``/api/storage_paths/``).

    VERIFIED_LIVE (3.1.2)::

        id, slug, name, path, match, matching_algorithm, is_insensitive,
        owner, user_can_change
    """

    model_config = ConfigDict(extra="ignore")

    id: int
    name: str
    slug: str | None = None
    path: str | None = None
    owner: int | None = None
    user_can_change: bool | None = None
    document_count: int | None = None


class MetadataKind(StrEnum):
    """Which metadata registry a resolved reference belongs to.

    Used wherever PaperWrench needs to say *which kind* of thing an id or
    name refers to - e.g. distinguishing a core-field reference
    (``document_type``, a fixed Paperless concept) from a custom-field
    reference (``custom_field:<id>``, open-ended and user-defined). Nothing
    downstream should have to guess this from shape alone.
    """

    TAG = "tag"
    CORRESPONDENT = "correspondent"
    DOCUMENT_TYPE = "document_type"
    STORAGE_PATH = "storage_path"
    CUSTOM_FIELD = "custom_field"


class FieldKind(StrEnum):
    """Core field vs custom field - the distinction M2 makes explicit.

    A *core* field (``title``, ``correspondent``, ``document_type``, ...)
    is a fixed part of the Paperless document schema. A *custom* field is
    user-defined, identified by an integer id, and carried in the
    ``custom_fields`` list. Code that walks "all fields of a document" must
    be able to tell these apart without inspecting names by convention.
    """

    CORE = "core"
    CUSTOM = "custom"


#: The fixed set of core document fields PaperWrench models (see
#: :class:`Document`). Anything not in here that shows up in a
#: ``custom_field_<id>`` context is, by construction, a custom field -
#: this is the allowlist FieldKind.of() below is built on.
CORE_DOCUMENT_FIELDS = frozenset(
    {
        "id",
        "title",
        "correspondent",
        "document_type",
        "storage_path",
        "tags",
        "created",
        "modified",
        "added",
        "archive_serial_number",
        "original_file_name",
        "owner",
        "user_can_change",
        "deleted_at",
    }
)


def field_kind(name: str) -> FieldKind:
    """Classify a field name as core or custom.

    ``name`` is expected to be either one of :data:`CORE_DOCUMENT_FIELDS`
    or a custom-field reference shaped like ``custom_field_<id>`` (the same
    convention Paperless itself uses for ordering - VERIFIED_SOURCE). This
    is the single place that decision is made, so the Filter Engine (M4)
    and Inspector (M5) do not each grow their own copy of it.
    """
    if name in CORE_DOCUMENT_FIELDS:
        return FieldKind.CORE
    return FieldKind.CUSTOM


class Document(BaseModel):
    """The subset of a Paperless document PaperWrench reads.

    VERIFIED_LIVE (3.1.2) - full key set observed on a real instance::

        added, archive_serial_number, archived_file_name, content,
        correspondent, created, created_date, custom_fields, deleted_at,
        document_type, duplicate_documents, id, is_shared_by_requester,
        mime_type, modified, notes, original_file_name, owner, page_count,
        root_document, storage_path, tags, title, user_can_change, versions
    """

    model_config = ConfigDict(extra="ignore")

    id: int
    title: str
    correspondent: int | None = None
    document_type: int | None = None
    storage_path: int | None = None
    tags: list[int] = Field(default_factory=list)
    created: str | None = None
    modified: str | None = None
    added: str | None = None
    archive_serial_number: int | None = None
    original_file_name: str | None = None
    owner: int | None = None
    custom_fields: list[CustomFieldValue] = Field(default_factory=list)

    # VERIFIED_LIVE (3.1.2): present and non-null on a normal document.
    # `user_can_change` is what a pre-flight permission check should read.
    user_can_change: bool | None = None
    deleted_at: str | None = None

    @property
    def custom_field_map(self) -> dict[int, Any]:
        """``{field_id: value}`` for convenient lookups."""
        return {item.field: item.value for item in self.custom_fields}

    def typed_custom_fields(
        self, definitions: dict[int, CustomField]
    ) -> dict[int, TypedCustomFieldValue]:
        """Typed view of every field in ``definitions``, ABSENT ones included.

        ``definitions`` is normally the Metadata Registry's
        ``custom_field_by_id`` mapping. Every field the registry knows
        about gets an entry here, even if this document does not carry it
        at all - that is exactly the ABSENT case
        :meth:`CustomField.typed_value` exists to represent, rather than
        silently omitting the field from the result.
        """
        present = {item.field: item for item in self.custom_fields}
        return {
            field_id: field.typed_value(present.get(field_id))
            for field_id, field in definitions.items()
        }


class Page(BaseModel, Generic[T]):
    """One page of a Paperless list response.

    VERIFIED_LIVE (3.1.2): under API v10 the envelope is exactly
    ``{count, next, previous, results}``.

    The v9 envelope additionally carries ``all`` (every matching id). That
    field is **absent in v10** and PaperWrench must not depend on it - which
    is precisely why the client pins v10 and paginates explicitly.
    """

    model_config = ConfigDict(extra="ignore")

    count: int
    next: str | None = None
    previous: str | None = None
    results: list[T]


class ConnectionStatus(BaseModel):
    """Result of a connection + compatibility probe.

    This is returned to the browser, so it must contain no secret. The URL is
    included because it is operator-supplied configuration, not a credential;
    the token never appears in any form, not even redacted-with-length.
    """

    model_config = ConfigDict(extra="ignore")

    configured: bool
    connected: bool
    compatible: bool
    url: str | None = None
    #: ``X-Api-Version``: the HIGHEST API version the server supports, not the
    #: negotiated one (VERIFIED_LIVE 3.1.2 - the middleware always reports
    #: ``ALLOWED_VERSIONS[-1]``).
    api_version: str | None = None
    paperless_version: str | None = None
    requested_api_version: int | None = None
    document_count: int | None = None
    error_code: str | None = None
    error_message: str | None = None


def merge_custom_fields(
    existing: list[CustomFieldValue] | list[dict[str, Any]],
    updates: list[CustomFieldValue] | list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge custom field values by field id, returning the COMPLETE state.

    **This function is the mitigation for the single most destructive
    behaviour in the Paperless API, and it is not optional.**

    VERIFIED_LIVE on Paperless-ngx 3.1.2. A document carrying five custom
    fields was sent a PATCH containing one of them::

        PATCH /api/documents/1/  {"custom_fields": [{"field": 2, ...}]}

    The response was ``200 OK``. The document afterwards had **one** custom
    field: the four omitted ones were deleted. No warning, no error, and
    Paperless does not version custom field instances, so the values are
    unrecoverable.

    The same document, patched with the merged full list produced by this
    function, kept all five fields with only the targeted one changed.

    So: never build a ``custom_fields`` payload by hand. Read the document,
    pass its current ``custom_fields`` as ``existing``, pass only what you
    want to change as ``updates``, and send the result.

    Neither argument is mutated.

    Ordering note: fields already on the document keep their relative order
    and updated values are replaced in place, so a merged payload does not
    gratuitously reshuffle the collection. Genuinely new fields are appended.
    """
    merged: dict[int, dict[str, Any]] = {}
    for item in existing:
        data = item.model_dump() if isinstance(item, CustomFieldValue) else dict(item)
        merged[int(data["field"])] = data
    for item in updates:
        data = item.model_dump() if isinstance(item, CustomFieldValue) else dict(item)
        merged[int(data["field"])] = data
    return list(merged.values())
