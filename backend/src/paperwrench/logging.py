"""Structured logging with mandatory secret redaction.

Redaction happens in a structlog *processor*, not at call sites. That is
deliberate: an ad-hoc ``logger.info("req", headers=headers)`` somewhere in the
codebase must not be able to leak the Paperless token. See ADR-0002.
"""

from __future__ import annotations

import logging
import sys
from typing import Any
from typing import cast

import structlog

REDACTED = "***redacted***"

#: Event-dict keys whose value is always replaced, whatever it contains.
SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "token",
        "api_token",
        "paperless_token",
        "password",
        "secret",
        "cookie",
        "set-cookie",
        "x-api-key",
        "credentials",
        "credential_key",
        "password_hash",
        "encrypted_token",
    }
)

#: Values registered at startup and scrubbed from every log record.
_secret_values: set[str] = set()

#: Field values (titles, custom field values...) can contain personal data.
MAX_VALUE_LENGTH = 200


def register_secret(value: str | None) -> None:
    """Register a literal secret to be scrubbed from all future log output."""
    if value and len(value) >= 8:
        _secret_values.add(value)


def reset_secrets() -> None:
    """Clear registered secrets (test helper)."""
    _secret_values.clear()


def _scrub_text(text: str) -> str:
    for secret in _secret_values:
        if secret in text:
            text = text.replace(secret, REDACTED)
    return text


def scrub_secrets(text: str) -> str:
    """Replace every registered secret in ``text``.

    Public because logs are not the only sink that can leak a credential.
    Exception messages get serialised into API responses and rendered in the
    browser, so anything echoing an upstream response body must pass through
    here first - a Paperless (or a reverse proxy in front of it) that reflects
    the ``Authorization`` header in an error page would otherwise hand the
    token straight to the frontend.
    """
    return _scrub_text(text)


def _redact(value: Any, key: str | None = None) -> Any:
    """Recursively redact sensitive keys and known secret values."""
    if key is not None and key.lower() in SENSITIVE_KEYS:
        return REDACTED
    if isinstance(value, str):
        scrubbed = _scrub_text(value)
        if len(scrubbed) > MAX_VALUE_LENGTH:
            return scrubbed[:MAX_VALUE_LENGTH] + "...[truncated]"
        return scrubbed
    if isinstance(value, dict):
        return {k: _redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list | tuple):
        items = [_redact(v) for v in cast("list[Any]", value)]
        return items if isinstance(value, list) else tuple(items)
    return value


def redaction_processor(
    _logger: Any, _name: str, event_dict: structlog.types.EventDict
) -> structlog.types.EventDict:
    """structlog processor scrubbing secrets from every record."""
    return cast("structlog.types.EventDict", _redact(dict(event_dict)))


def configure_logging(level: str = "INFO", fmt: str = "json") -> None:
    """Configure structlog and route stdlib logging through it."""
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, level.upper(), logging.INFO),
        force=True,
    )
    # Uvicorn's access log duplicates our own request logging.
    logging.getLogger("uvicorn.access").disabled = True

    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer()
        if fmt == "json"
        else structlog.dev.ConsoleRenderer(colors=False)
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            redaction_processor,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a bound structlog logger."""
    return cast("structlog.stdlib.BoundLogger", structlog.get_logger(name))
