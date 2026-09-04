"""Metadata Registry: id/name lookups for Paperless reference data.

This is the boundary M2 was scoped to finish: the rest of PaperWrench looks
up a tag, correspondent, document type, storage path or custom field through
this registry - never by re-parsing a raw Paperless list response, and never
by guessing at uniqueness.

Two rules govern this module:

1. **Never arbitrarily pick "the first" match for a name lookup.** If more
   than one object of the same kind has the exact same name, that is an
   :class:`AmbiguousMetadataName`, not a silent choice. (VERIFIED_LIVE on
   3.1.2: tags and custom fields both enforce a per-owner name uniqueness
   constraint server-side, so a same-owner collision cannot happen through
   Paperless's own UI/API today - but nothing stops two objects owned by
   *different* users from sharing a name, and PaperWrench must not assume
   single-owner operation forever. The registry enforces uniqueness itself
   rather than trusting that assumption.)
2. **The cache is not the source of truth.** This registry is a read-through
   cache with an explicit TTL, refresh and invalidate. Paperless remains
   authoritative; a stale registry answer is a known, bounded staleness
   window, not a second database.

Name resolution is deliberately an *exact* string match. No accent-folding,
no case-insensitivity, no trimming beyond what Paperless itself already did
on write. A resolver that quietly normalises names would resolve
"Étatissement" and "Etablissement" - two different, deliberately distinct
custom fields in the Golden Dataset (see scripts/seed_dev_golden_dataset.py)
- to the same object, which is exactly the kind of silent collapsing this
project has spent M1 avoiding elsewhere.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import ClassVar
from typing import Generic
from typing import TypeVar

from paperwrench.errors import ErrorCode
from paperwrench.errors import PaperWrenchError
from paperwrench.paperless.client import PaperlessClient
from paperwrench.paperless.models import Correspondent
from paperwrench.paperless.models import CustomField
from paperwrench.paperless.models import DocumentType
from paperwrench.paperless.models import MetadataKind
from paperwrench.paperless.models import StoragePath
from paperwrench.paperless.models import Tag

M = TypeVar("M")


class AmbiguousMetadataName(PaperWrenchError):
    """More than one object of the same kind shares the requested name.

    Raised instead of arbitrarily returning one of them. The caller must
    disambiguate - typically by id, which is always unique.
    """

    status_code = 409
    code = ErrorCode.CONFLICT

    def __init__(self, kind: MetadataKind, name: str, matching_ids: list[int]) -> None:
        super().__init__(
            f"{kind.value} name {name!r} is ambiguous: {len(matching_ids)} objects match.",
            details={"kind": kind.value, "name": name, "matching_ids": matching_ids},
        )
        self.kind = kind
        self.name = name
        self.matching_ids = matching_ids


class MetadataNotFoundError(PaperWrenchError):
    """No object of the requested kind matches the given id or name."""

    status_code = 404
    code = ErrorCode.NOT_FOUND

    def __init__(
        self, kind: MetadataKind, *, id: int | None = None, name: str | None = None
    ) -> None:
        if id is not None:
            message = f"No {kind.value} with id {id}."
        else:
            message = f"No {kind.value} named {name!r}."
        super().__init__(message, details={"kind": kind.value, "id": id, "name": name})
        self.kind = kind


@dataclass
class _CacheEntry(Generic[M]):
    """One metadata kind's cached state.

    ``fetched_at`` is a monotonic timestamp (``time.monotonic()``), never
    wall-clock time - wall-clock can jump (NTP, DST, a container pause) in
    ways that would make "expired" lie in either direction.
    """

    by_id: dict[int, M] = field(default_factory=dict)
    fetched_at: float | None = None

    def is_expired(self, ttl_seconds: float, *, now: float) -> bool:
        if self.fetched_at is None:
            return True
        return (now - self.fetched_at) > ttl_seconds

    def invalidate(self) -> None:
        self.fetched_at = None


DEFAULT_TTL_SECONDS = 60.0


class MetadataRegistry:
    """Cached id/name lookups over Paperless reference data.

    Not a general-purpose ORM cache: it holds exactly the five metadata
    kinds M2 scopes (tags, correspondents, document types, storage paths,
    custom fields), each independently cacheable, refreshable and
    invalidatable. There is no SQLite table behind this - it lives entirely
    in process memory and is rebuilt from Paperless whenever it is stale or
    was explicitly invalidated.

    Every ``*_by_name`` lookup does a fresh linear scan of the current
    snapshot rather than maintaining a separate name index, so an ambiguous
    match is always detected from the live snapshot, never a possibly-stale
    secondary index.
    """

    def __init__(
        self, client: PaperlessClient, *, ttl_seconds: float = DEFAULT_TTL_SECONDS
    ) -> None:
        self._client = client
        self._ttl_seconds = ttl_seconds
        self._tags: _CacheEntry[Tag] = _CacheEntry()
        self._correspondents: _CacheEntry[Correspondent] = _CacheEntry()
        self._document_types: _CacheEntry[DocumentType] = _CacheEntry()
        self._storage_paths: _CacheEntry[StoragePath] = _CacheEntry()
        self._custom_fields: _CacheEntry[CustomField] = _CacheEntry()

    # ------------------------------------------------------------- freshness
    def invalidate(self, kind: MetadataKind | None = None) -> None:
        """Force the next lookup of ``kind`` (or all kinds) to refetch.

        Use this after a write that is known to change reference data - e.g.
        creating a tag - rather than waiting out the TTL.
        """
        entries = self._entries() if kind is None else [self._entry_for(kind)]
        for entry in entries:
            entry.invalidate()

    async def refresh(self, kind: MetadataKind | None = None) -> None:
        """Unconditionally refetch ``kind`` (or all kinds) right now."""
        kinds = list(MetadataKind) if kind is None else [kind]
        for one in kinds:
            await self._fetch(one)

    def _entries(self) -> list[_CacheEntry[Any]]:
        return [
            self._tags,
            self._correspondents,
            self._document_types,
            self._storage_paths,
            self._custom_fields,
        ]

    def _entry_for(self, kind: MetadataKind) -> _CacheEntry[Any]:
        mapping: dict[MetadataKind, _CacheEntry[Any]] = {
            MetadataKind.TAG: self._tags,
            MetadataKind.CORRESPONDENT: self._correspondents,
            MetadataKind.DOCUMENT_TYPE: self._document_types,
            MetadataKind.STORAGE_PATH: self._storage_paths,
            MetadataKind.CUSTOM_FIELD: self._custom_fields,
        }
        return mapping[kind]

    _FETCHERS: ClassVar[dict[MetadataKind, str]] = {
        MetadataKind.TAG: "list_tags",
        MetadataKind.CORRESPONDENT: "list_correspondents",
        MetadataKind.DOCUMENT_TYPE: "list_document_types",
        MetadataKind.STORAGE_PATH: "list_storage_paths",
        MetadataKind.CUSTOM_FIELD: "list_custom_fields",
    }

    async def _ensure_fresh(self, kind: MetadataKind) -> _CacheEntry[object]:
        entry = self._entry_for(kind)
        if entry.is_expired(self._ttl_seconds, now=time.monotonic()):
            await self._fetch(kind)
        return entry

    async def _fetch(self, kind: MetadataKind) -> None:
        entry = self._entry_for(kind)
        method_name = self._FETCHERS[kind]
        method: Callable[[], Awaitable[list[object]]] = getattr(self._client, method_name)
        items = await method()
        entry.by_id = {item.id: item for item in items}  # type: ignore[attr-defined]
        entry.fetched_at = time.monotonic()

    # ------------------------------------------------------------- by id
    async def _by_id(self, kind: MetadataKind, object_id: int) -> object:
        entry = await self._ensure_fresh(kind)
        try:
            return entry.by_id[object_id]
        except KeyError:
            raise MetadataNotFoundError(kind, id=object_id) from None

    async def _by_name(self, kind: MetadataKind, name: str) -> object:
        entry = await self._ensure_fresh(kind)
        matches = [item for item in entry.by_id.values() if getattr(item, "name") == name]  # noqa: B009
        if not matches:
            raise MetadataNotFoundError(kind, name=name)
        if len(matches) > 1:
            raise AmbiguousMetadataName(
                kind, name, sorted(item.id for item in matches)  # type: ignore[attr-defined]
            )
        return matches[0]

    # ------------------------------------------------------------- tags
    async def tag_by_id(self, tag_id: int) -> Tag:
        result = await self._by_id(MetadataKind.TAG, tag_id)
        assert isinstance(result, Tag)
        return result

    async def tag_by_name(self, name: str) -> Tag:
        result = await self._by_name(MetadataKind.TAG, name)
        assert isinstance(result, Tag)
        return result

    # ------------------------------------------------------- correspondents
    async def correspondent_by_id(self, correspondent_id: int) -> Correspondent:
        result = await self._by_id(MetadataKind.CORRESPONDENT, correspondent_id)
        assert isinstance(result, Correspondent)
        return result

    async def correspondent_by_name(self, name: str) -> Correspondent:
        result = await self._by_name(MetadataKind.CORRESPONDENT, name)
        assert isinstance(result, Correspondent)
        return result

    # ------------------------------------------------------- document types
    async def document_type_by_id(self, document_type_id: int) -> DocumentType:
        result = await self._by_id(MetadataKind.DOCUMENT_TYPE, document_type_id)
        assert isinstance(result, DocumentType)
        return result

    async def document_type_by_name(self, name: str) -> DocumentType:
        result = await self._by_name(MetadataKind.DOCUMENT_TYPE, name)
        assert isinstance(result, DocumentType)
        return result

    # --------------------------------------------------------- storage paths
    async def storage_path_by_id(self, storage_path_id: int) -> StoragePath:
        result = await self._by_id(MetadataKind.STORAGE_PATH, storage_path_id)
        assert isinstance(result, StoragePath)
        return result

    async def storage_path_by_name(self, name: str) -> StoragePath:
        result = await self._by_name(MetadataKind.STORAGE_PATH, name)
        assert isinstance(result, StoragePath)
        return result

    # --------------------------------------------------------- custom fields
    async def custom_field_by_id(self, field_id: int) -> CustomField:
        result = await self._by_id(MetadataKind.CUSTOM_FIELD, field_id)
        assert isinstance(result, CustomField)
        return result

    async def custom_field_by_name(self, name: str) -> CustomField:
        result = await self._by_name(MetadataKind.CUSTOM_FIELD, name)
        assert isinstance(result, CustomField)
        return result

    # ------------------------------------------------------------- snapshots
    async def all_tags(self) -> list[Tag]:
        entry = await self._ensure_fresh(MetadataKind.TAG)
        return list(entry.by_id.values())  # type: ignore[arg-type]

    async def all_correspondents(self) -> list[Correspondent]:
        entry = await self._ensure_fresh(MetadataKind.CORRESPONDENT)
        return list(entry.by_id.values())  # type: ignore[arg-type]

    async def all_document_types(self) -> list[DocumentType]:
        entry = await self._ensure_fresh(MetadataKind.DOCUMENT_TYPE)
        return list(entry.by_id.values())  # type: ignore[arg-type]

    async def all_storage_paths(self) -> list[StoragePath]:
        entry = await self._ensure_fresh(MetadataKind.STORAGE_PATH)
        return list(entry.by_id.values())  # type: ignore[arg-type]

    async def all_custom_fields(self) -> list[CustomField]:
        entry = await self._ensure_fresh(MetadataKind.CUSTOM_FIELD)
        return list(entry.by_id.values())  # type: ignore[arg-type]
