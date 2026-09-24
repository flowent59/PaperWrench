"""Pure shared validation for supported custom-field write values."""

from __future__ import annotations

from contextlib import suppress
from datetime import date
from typing import Any

from paperwrench.paperless.errors import PaperlessValidationError
from paperwrench.paperless.models import CustomField
from paperwrench.paperless.models import MonetaryAmount

EDITABLE_CUSTOM_TYPES = frozenset(
    {"string", "longtext", "monetary", "select", "date", "boolean", "integer"}
)


def validate_custom_present(value: Any, definition: CustomField) -> None:
    """Reject values Paperless would not store for the supported M5/M6 types."""
    if definition.data_type not in EDITABLE_CUSTOM_TYPES:
        raise PaperlessValidationError("This custom-field type is read-only.")
    valid = False
    data_type = definition.data_type
    if data_type in {"string", "longtext"}:
        valid = isinstance(value, str)
    elif data_type == "boolean":
        valid = type(value) is bool
    elif data_type == "integer":
        valid = type(value) is int and -(2**31) <= value < 2**31
    elif data_type == "select":
        valid = isinstance(value, str) and any(
            option.get("id") == value for option in definition.select_options
        )
    elif data_type == "monetary" and isinstance(value, str):
        try:
            MonetaryAmount.parse(value)
            valid = True
        except ValueError:
            pass
    elif data_type == "date" and isinstance(value, str):
        with suppress(ValueError):
            valid = date.fromisoformat(value).isoformat() == value
    if not valid:
        raise PaperlessValidationError(
            "Value does not match the custom-field type or allowed option IDs.",
            details={"field_id": definition.id, "data_type": data_type},
        )
