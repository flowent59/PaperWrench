"""The one and only boundary between PaperWrench and Paperless-ngx.

Nothing outside this package may build an HTTP request to Paperless. Every
call goes through :class:`~paperwrench.paperless.client.PaperlessClient`, so
that authentication, API version negotiation, timeouts, error normalisation
and - above all - the custom-field read-modify-write rule are enforced in
exactly one place (ADR-0002).

If you find yourself importing ``httpx`` in a module outside this package to
talk to Paperless, that is the bug.
"""

from __future__ import annotations

from paperwrench.paperless.client import PaperlessClient
from paperwrench.paperless.errors import PaperlessApiError
from paperwrench.paperless.errors import PaperlessConflictError
from paperwrench.paperless.errors import PaperlessNotFoundError
from paperwrench.paperless.errors import PaperlessValidationError
from paperwrench.paperless.models import ConnectionStatus
from paperwrench.paperless.models import Correspondent
from paperwrench.paperless.models import CustomField
from paperwrench.paperless.models import CustomFieldDataType
from paperwrench.paperless.models import CustomFieldValue
from paperwrench.paperless.models import CustomFieldValueKind
from paperwrench.paperless.models import Document
from paperwrench.paperless.models import DocumentType
from paperwrench.paperless.models import FieldKind
from paperwrench.paperless.models import MetadataKind
from paperwrench.paperless.models import MonetaryAmount
from paperwrench.paperless.models import Page
from paperwrench.paperless.models import StoragePath
from paperwrench.paperless.models import Tag
from paperwrench.paperless.models import TypedCustomFieldValue
from paperwrench.paperless.models import field_kind
from paperwrench.paperless.models import merge_custom_fields
from paperwrench.paperless.registry import AmbiguousMetadataName
from paperwrench.paperless.registry import MetadataNotFoundError
from paperwrench.paperless.registry import MetadataRegistry

__all__ = [
    "AmbiguousMetadataName",
    "ConnectionStatus",
    "Correspondent",
    "CustomField",
    "CustomFieldDataType",
    "CustomFieldValue",
    "CustomFieldValueKind",
    "Document",
    "DocumentType",
    "FieldKind",
    "MetadataKind",
    "MetadataNotFoundError",
    "MetadataRegistry",
    "MonetaryAmount",
    "Page",
    "PaperlessApiError",
    "PaperlessClient",
    "PaperlessConflictError",
    "PaperlessNotFoundError",
    "PaperlessValidationError",
    "StoragePath",
    "Tag",
    "TypedCustomFieldValue",
    "field_kind",
    "merge_custom_fields",
]
