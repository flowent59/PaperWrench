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
from paperwrench.paperless.models import CustomField
from paperwrench.paperless.models import CustomFieldDataType
from paperwrench.paperless.models import CustomFieldValue
from paperwrench.paperless.models import Document
from paperwrench.paperless.models import Page
from paperwrench.paperless.models import merge_custom_fields

__all__ = [
    "ConnectionStatus",
    "CustomField",
    "CustomFieldDataType",
    "CustomFieldValue",
    "Document",
    "Page",
    "PaperlessApiError",
    "PaperlessClient",
    "PaperlessConflictError",
    "PaperlessNotFoundError",
    "PaperlessValidationError",
    "merge_custom_fields",
]
