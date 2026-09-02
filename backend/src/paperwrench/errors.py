"""Uniform error envelope shared by every endpoint.

The frontend reads ``error.code`` to decide what to render, so codes are part
of the API contract and must not be renamed casually.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel


class ErrorCode(StrEnum):
    """Stable, machine-readable error codes."""

    # Paperless integration
    PAPERLESS_NOT_CONFIGURED = "PAPERLESS_NOT_CONFIGURED"
    PAPERLESS_UNREACHABLE = "PAPERLESS_UNREACHABLE"
    PAPERLESS_UNAUTHORIZED = "PAPERLESS_UNAUTHORIZED"
    PAPERLESS_INCOMPATIBLE = "PAPERLESS_INCOMPATIBLE"
    PAPERLESS_ERROR = "PAPERLESS_ERROR"

    # Filters / transformations (M4+)
    FILTER_NOT_COMPILABLE = "FILTER_NOT_COMPILABLE"
    TEMPLATE_INVALID = "TEMPLATE_INVALID"
    TEMPLATE_UNRESOLVED = "TEMPLATE_UNRESOLVED"

    # Jobs (M8+)
    PREVIEW_STALE = "PREVIEW_STALE"
    JOB_CONFLICT = "JOB_CONFLICT"
    JOB_NOT_RESUMABLE = "JOB_NOT_RESUMABLE"

    # Generic
    NOT_FOUND = "NOT_FOUND"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    FORBIDDEN_ORIGIN = "FORBIDDEN_ORIGIN"
    CONFLICT = "CONFLICT"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    SINGLE_INSTANCE_VIOLATION = "SINGLE_INSTANCE_VIOLATION"


class ErrorDetail(BaseModel):
    """Body of an error response."""

    code: ErrorCode
    message: str
    details: dict[str, Any] | None = None
    retryable: bool = False


class ErrorResponse(BaseModel):
    """``{"error": {...}}`` envelope."""

    error: ErrorDetail


class PaperWrenchError(Exception):
    """Base class for errors that map onto the envelope."""

    status_code: int = 500
    code: ErrorCode = ErrorCode.INTERNAL_ERROR
    retryable: bool = False

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        status_code: int | None = None,
        code: ErrorCode | None = None,
        retryable: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details
        if status_code is not None:
            self.status_code = status_code
        if code is not None:
            self.code = code
        if retryable is not None:
            self.retryable = retryable

    def to_response(self) -> ErrorResponse:
        return ErrorResponse(
            error=ErrorDetail(
                code=self.code,
                message=self.message,
                details=self.details,
                retryable=self.retryable,
            )
        )


class PaperlessNotConfiguredError(PaperWrenchError):
    status_code = 503
    code = ErrorCode.PAPERLESS_NOT_CONFIGURED


class PaperlessUnreachableError(PaperWrenchError):
    status_code = 502
    code = ErrorCode.PAPERLESS_UNREACHABLE
    retryable = True


class PaperlessUnauthorizedError(PaperWrenchError):
    status_code = 502
    code = ErrorCode.PAPERLESS_UNAUTHORIZED


class PaperlessIncompatibleError(PaperWrenchError):
    status_code = 502
    code = ErrorCode.PAPERLESS_INCOMPATIBLE


class NotFoundError(PaperWrenchError):
    status_code = 404
    code = ErrorCode.NOT_FOUND


class SingleInstanceViolationError(PaperWrenchError):
    """Raised when a second PaperWrench instance tries to start (ADR-0006)."""

    status_code = 500
    code = ErrorCode.SINGLE_INSTANCE_VIOLATION
