"""Paperless-specific errors, mapped onto PaperWrench's uniform envelope.

Every failure mode below was reproduced against a real Paperless-ngx 3.1.2
instance; the observed status codes and bodies are quoted in the docstrings so
that the mapping can be re-checked rather than trusted.

One rule governs this module: **the token must never reach an exception
message**. Exceptions get logged, serialised into API responses and shown in
browsers. The client therefore never puts request headers into an error.
"""

from __future__ import annotations

from typing import Any

from paperwrench.errors import ErrorCode
from paperwrench.errors import PaperWrenchError
from paperwrench.logging import scrub_secrets

# Response bodies can be large (a full HTML error page from a misconfigured
# reverse proxy, for instance). Keep only enough to diagnose.
MAX_BODY_EXCERPT = 500


class PaperlessApiError(PaperWrenchError):
    """Paperless answered, but with an error status.

    VERIFIED_LIVE (3.1.2): the API returns JSON error bodies such as
    ``{"detail": "Invalid token."}`` for 401 and
    ``{"detail": "No Document matches the given query."}`` for 404.
    """

    status_code = 502
    code = ErrorCode.PAPERLESS_ERROR

    def __init__(
        self,
        message: str,
        *,
        upstream_status: int | None = None,
        upstream_body: str | None = None,
        details: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        merged: dict[str, Any] = dict(details or {})
        if upstream_status is not None:
            merged["upstream_status"] = upstream_status
        if upstream_body:
            # Scrub BEFORE truncating: a token split across the cut-off would
            # otherwise survive as a recognisable fragment.
            merged["upstream_body"] = scrub_secrets(upstream_body)[:MAX_BODY_EXCERPT]
        super().__init__(scrub_secrets(message), details=merged or None, **kwargs)
        self.upstream_status = upstream_status


class PaperlessNotFoundError(PaperlessApiError):
    """404 from Paperless.

    VERIFIED_LIVE (3.1.2): ``GET /api/documents/999999/`` and
    ``PATCH /api/documents/999999/`` both return 404 with
    ``{"detail": "No Document matches the given query."}``.

    Note this is a 404 *upstream*, not a 404 of the PaperWrench API: the
    PaperWrench route existed, the Paperless object did not.
    """

    status_code = 404
    code = ErrorCode.NOT_FOUND


class PaperlessValidationError(PaperlessApiError):
    """400 from Paperless: the payload was rejected.

    VERIFIED_LIVE (3.1.2): writing ``"EUR450,00"`` (comma decimal) to a
    monetary custom field returns 400 with a per-field error list::

        {"custom_fields": [{"non_field_errors": ["Must be a two-decimal
         number with optional currency code e.g. GBP123.45"]}, {}, ...]}

    Importantly, the document is left untouched when this happens - the whole
    PATCH is rejected atomically.
    """

    status_code = 422
    code = ErrorCode.VALIDATION_ERROR


class PaperlessConflictError(PaperlessApiError):
    """409 from Paperless.

    ASSUMED: not reproduced on 3.1.2 during M1. Kept because bulk operations
    and the trash endpoints are documented to be able to conflict, and because
    a job runner needs somewhere to route the condition.

    NON-RETRYABLE BY DEFAULT. A 409 means Paperless refused the request
    because of the *state* it found (a conflicting write, a stale
    precondition, a resource in the wrong state) - not because of a
    transient transport problem. Blindly retrying a conflict risks silently
    converting it into an automatic retry loop before anyone has understood
    *why* the conflict happened, which is exactly the kind of behaviour the
    future Job Engine and forward conflict detection must never exhibit.

    If a specific Paperless endpoint is later proven (with documentation and
    live tests) to return 409 for a genuinely transient reason, define a
    dedicated exception for that endpoint rather than flipping this default.
    """

    status_code = 409
    code = ErrorCode.CONFLICT
    retryable = False
