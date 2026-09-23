"""Single-process document mutation contract (ADR-0012).

Locks coordinate cooperating clients only. They cannot lock Paperless itself.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import ValidationError

from paperwrench.paperless.errors import PaperlessValidationError
from paperwrench.paperless.models import Document


class CorePatch(BaseModel):
    """Closed wire allowlist; aliases and nested arbitrary payloads are refused."""

    model_config = ConfigDict(extra="forbid", strict=True)

    title: str = Field(default="", min_length=1, max_length=128)
    correspondent: int | None = Field(default=None, gt=0)
    document_type: int | None = Field(default=None, gt=0)
    storage_path: int | None = Field(default=None, gt=0)
    tags: list[int] = Field(default_factory=list)
    created: str | None = None
    archive_serial_number: int | None = Field(default=None, ge=0)


def core_payload(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        parsed = CorePatch.model_validate(payload)
    except ValidationError as exc:
        raise PaperlessValidationError("Unsupported document write payload.") from exc
    result = parsed.model_dump(exclude_unset=True)
    if "title" in result and not result["title"].strip():
        raise PaperlessValidationError("Title must contain a non-whitespace character.")
    if "tags" in result and any(tag <= 0 for tag in result["tags"]):
        raise PaperlessValidationError("Tag IDs must be positive integers.")
    if "created" in result:
        from datetime import date

        try:
            value = result["created"]
            if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
                raise ValueError
        except ValueError as exc:
            raise PaperlessValidationError("Created must be a YYYY-MM-DD date.") from exc
    return result


def revision(document: Document) -> str:
    """Opaque optimistic precondition, including unknown custom-field definitions.

    JSON comparison preserves bool vs integer and absent vs explicit null.
    Collection order is irrelevant; values are never coerced or rewritten.
    """
    data = document.model_dump(mode="json")
    data["tags"] = sorted(document.tags)
    data["custom_fields"] = sorted(data["custom_fields"], key=lambda item: item["field"])
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


@dataclass
class MutationResult:
    before: Document
    intended: dict[str, Any]
    written: Document


class DocumentMutationCoordinator:
    """Share this instance across all writers for one Paperless deployment.

    The app's lifecycle client owns it; later Jobs must use that same client
    or explicitly inject its coordinator. No durable/distributed guarantees.
    Entries include waiters and are removed even on cancellation.
    """

    def __init__(self) -> None:
        self._entries: dict[int, tuple[asyncio.Lock, int]] = {}

    @asynccontextmanager
    async def hold(self, document_id: int) -> AsyncIterator[None]:
        lock, users = self._entries.get(document_id, (asyncio.Lock(), 0))
        self._entries[document_id] = (lock, users + 1)
        try:
            async with lock:
                yield
        finally:
            _, users = self._entries[document_id]
            if users == 1:
                del self._entries[document_id]
            else:
                self._entries[document_id] = (lock, users - 1)
