"""Typed views over the Paperless-ngx payloads PaperWrench actually reads.

These models are deliberately *partial*. Paperless documents carry 26 keys in
3.1.2; modelling all of them would turn every upstream addition into a
breaking change here. ``model_config = ConfigDict(extra="ignore")`` means we
describe what we use and tolerate the rest.

Everything in this file was shaped by observation against a real 3.1.2
instance, not by reading the OpenAPI schema.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any
from typing import Generic
from typing import TypeVar

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

T = TypeVar("T")


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


class CustomField(BaseModel):
    """A custom field *definition* (``/api/custom_fields/``)."""

    model_config = ConfigDict(extra="ignore")

    id: int
    name: str
    data_type: CustomFieldDataType
    extra_data: dict[str, Any] = Field(default_factory=dict)

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


class CustomFieldValue(BaseModel):
    """A custom field *instance* on a document.

    VERIFIED_LIVE (3.1.2): serialised as ``{"field": <int id>, "value": ...}``.
    ``value`` is polymorphic: ``str`` for string/monetary/date/select,
    ``bool`` for boolean, ``None`` for an explicitly null value.
    """

    model_config = ConfigDict(extra="ignore")

    field: int
    value: Any = None


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
