"""Unit tests for the Metadata Registry (id/name lookups, TTL cache).

No HTTP here: the registry is exercised against a small fake standing in for
``PaperlessClient``, so these tests are about the registry's own behaviour
(ambiguity detection, caching, invalidation) rather than about Paperless.
"""

from __future__ import annotations

from typing import Any

import pytest

from paperwrench.paperless.models import Correspondent
from paperwrench.paperless.models import CustomField
from paperwrench.paperless.models import DocumentType
from paperwrench.paperless.models import MetadataKind
from paperwrench.paperless.models import StoragePath
from paperwrench.paperless.models import Tag
from paperwrench.paperless.registry import AmbiguousMetadataName
from paperwrench.paperless.registry import MetadataNotFoundError
from paperwrench.paperless.registry import MetadataRegistry


class _FakeClock:
    """A controllable stand-in for ``time.monotonic``."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _FakeClient:
    """Stands in for PaperlessClient: returns whatever list is queued.

    Call counts are tracked per method so a test can assert the registry
    only refetches when it is supposed to - the entire point of a cache.
    """

    def __init__(self) -> None:
        self.tags: list[Tag] = []
        self.correspondents: list[Correspondent] = []
        self.document_types: list[DocumentType] = []
        self.storage_paths: list[StoragePath] = []
        self.custom_fields: list[CustomField] = []
        self.calls: dict[str, int] = {
            "list_tags": 0,
            "list_correspondents": 0,
            "list_document_types": 0,
            "list_storage_paths": 0,
            "list_custom_fields": 0,
        }

    async def list_tags(self) -> list[Tag]:
        self.calls["list_tags"] += 1
        return self.tags

    async def list_correspondents(self) -> list[Correspondent]:
        self.calls["list_correspondents"] += 1
        return self.correspondents

    async def list_document_types(self) -> list[DocumentType]:
        self.calls["list_document_types"] += 1
        return self.document_types

    async def list_storage_paths(self) -> list[StoragePath]:
        self.calls["list_storage_paths"] += 1
        return self.storage_paths

    async def list_custom_fields(self) -> list[CustomField]:
        self.calls["list_custom_fields"] += 1
        return self.custom_fields


def _tag(id: int, name: str) -> Tag:
    return Tag.model_validate({"id": id, "name": name})


def _correspondent(id: int, name: str) -> Correspondent:
    return Correspondent.model_validate({"id": id, "name": name})


def _document_type(id: int, name: str) -> DocumentType:
    return DocumentType.model_validate({"id": id, "name": name})


def _storage_path(id: int, name: str) -> StoragePath:
    return StoragePath.model_validate({"id": id, "name": name})


def _custom_field(id: int, name: str, data_type: str = "string") -> CustomField:
    return CustomField.model_validate({"id": id, "name": name, "data_type": data_type})


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _FakeClock:
    fake = _FakeClock()
    monkeypatch.setattr("paperwrench.paperless.registry.time.monotonic", fake)
    return fake


@pytest.fixture
def fake_client() -> _FakeClient:
    return _FakeClient()


@pytest.fixture
def registry(fake_client: _FakeClient) -> MetadataRegistry:
    return MetadataRegistry(fake_client, ttl_seconds=60.0)  # type: ignore[arg-type]


class TestByIdAndByName:
    async def test_tag_by_id(
        self, registry: MetadataRegistry, fake_client: _FakeClient
    ) -> None:
        fake_client.tags = [_tag(1, "Urgent"), _tag(2, "Normal")]
        tag = await registry.tag_by_id(2)
        assert tag.name == "Normal"

    async def test_tag_by_name(
        self, registry: MetadataRegistry, fake_client: _FakeClient
    ) -> None:
        fake_client.tags = [_tag(1, "Urgent"), _tag(2, "Normal")]
        tag = await registry.tag_by_name("Urgent")
        assert tag.id == 1

    async def test_unknown_id_raises_not_found(
        self, registry: MetadataRegistry, fake_client: _FakeClient
    ) -> None:
        fake_client.tags = [_tag(1, "Urgent")]
        with pytest.raises(MetadataNotFoundError) as excinfo:
            await registry.tag_by_id(999)
        assert excinfo.value.kind is MetadataKind.TAG

    async def test_unknown_name_raises_not_found(
        self, registry: MetadataRegistry, fake_client: _FakeClient
    ) -> None:
        fake_client.tags = [_tag(1, "Urgent")]
        with pytest.raises(MetadataNotFoundError):
            await registry.tag_by_name("does-not-exist")

    async def test_name_lookup_is_an_exact_match_no_normalisation(
        self, registry: MetadataRegistry, fake_client: _FakeClient
    ) -> None:
        """Étatissement vs Etablissement: two real, deliberately distinct
        custom fields in the Golden Dataset. The registry must never fold
        accents or case to "help" a lookup - that would silently merge two
        different objects.
        """
        fake_client.custom_fields = [
            _custom_field(4, "Etablissement"),
            _custom_field(15, "Établissement"),
        ]
        exact = await registry.custom_field_by_name("Établissement")
        assert exact.id == 15
        with pytest.raises(MetadataNotFoundError):
            await registry.custom_field_by_name("établissement")  # different case

    async def test_correspondent_document_type_storage_path_lookups(
        self, registry: MetadataRegistry, fake_client: _FakeClient
    ) -> None:
        fake_client.correspondents = [_correspondent(1, "Clinique Saint-Roch")]
        fake_client.document_types = [_document_type(1, "Relevé de vacations")]
        fake_client.storage_paths = [_storage_path(1, "Archives")]

        assert (await registry.correspondent_by_id(1)).name == "Clinique Saint-Roch"
        assert (await registry.correspondent_by_name("Clinique Saint-Roch")).id == 1
        assert (await registry.document_type_by_id(1)).name == "Relevé de vacations"
        assert (await registry.document_type_by_name("Relevé de vacations")).id == 1
        assert (await registry.storage_path_by_id(1)).name == "Archives"
        assert (await registry.storage_path_by_name("Archives")).id == 1

    async def test_custom_field_by_id_and_name(
        self, registry: MetadataRegistry, fake_client: _FakeClient
    ) -> None:
        fake_client.custom_fields = [_custom_field(2, "Montant", "monetary")]
        assert (await registry.custom_field_by_id(2)).data_type.value == "monetary"
        assert (await registry.custom_field_by_name("Montant")).id == 2


class TestAmbiguousNames:
    async def test_two_objects_sharing_a_name_raise_ambiguous(
        self, registry: MetadataRegistry, fake_client: _FakeClient
    ) -> None:
        """Never arbitrarily pick "the first" match."""
        fake_client.tags = [_tag(1, "Same"), _tag(2, "Same")]
        with pytest.raises(AmbiguousMetadataName) as excinfo:
            await registry.tag_by_name("Same")
        assert excinfo.value.kind is MetadataKind.TAG
        assert excinfo.value.matching_ids == [1, 2]

    async def test_ambiguous_error_carries_every_matching_id(
        self, registry: MetadataRegistry, fake_client: _FakeClient
    ) -> None:
        fake_client.custom_fields = [
            _custom_field(1, "Dup"),
            _custom_field(2, "Dup"),
            _custom_field(3, "Dup"),
        ]
        with pytest.raises(AmbiguousMetadataName) as excinfo:
            await registry.custom_field_by_name("Dup")
        assert excinfo.value.matching_ids == [1, 2, 3]


class TestTtlCache:
    async def test_second_lookup_within_ttl_does_not_refetch(
        self, registry: MetadataRegistry, fake_client: _FakeClient, clock: _FakeClock
    ) -> None:
        fake_client.tags = [_tag(1, "Urgent")]
        await registry.tag_by_id(1)
        clock.advance(10.0)  # well within the 60s ttl
        await registry.tag_by_id(1)
        assert fake_client.calls["list_tags"] == 1

    async def test_lookup_past_ttl_refetches(
        self, registry: MetadataRegistry, fake_client: _FakeClient, clock: _FakeClock
    ) -> None:
        fake_client.tags = [_tag(1, "Urgent")]
        await registry.tag_by_id(1)
        clock.advance(61.0)  # past the 60s ttl
        await registry.tag_by_id(1)
        assert fake_client.calls["list_tags"] == 2

    async def test_different_kinds_have_independent_ttls(
        self, registry: MetadataRegistry, fake_client: _FakeClient, clock: _FakeClock
    ) -> None:
        fake_client.tags = [_tag(1, "Urgent")]
        fake_client.correspondents = [_correspondent(1, "X")]
        await registry.tag_by_id(1)
        await registry.correspondent_by_id(1)
        clock.advance(10.0)
        await registry.tag_by_id(1)
        await registry.correspondent_by_id(1)
        assert fake_client.calls["list_tags"] == 1
        assert fake_client.calls["list_correspondents"] == 1

    async def test_invalidate_forces_a_refetch_before_ttl_expires(
        self, registry: MetadataRegistry, fake_client: _FakeClient, clock: _FakeClock
    ) -> None:
        fake_client.tags = [_tag(1, "Urgent")]
        await registry.tag_by_id(1)
        registry.invalidate(MetadataKind.TAG)
        await registry.tag_by_id(1)
        assert fake_client.calls["list_tags"] == 2

    async def test_invalidate_without_kind_invalidates_everything(
        self, registry: MetadataRegistry, fake_client: _FakeClient
    ) -> None:
        fake_client.tags = [_tag(1, "Urgent")]
        fake_client.correspondents = [_correspondent(1, "X")]
        await registry.tag_by_id(1)
        await registry.correspondent_by_id(1)
        registry.invalidate()
        await registry.tag_by_id(1)
        await registry.correspondent_by_id(1)
        assert fake_client.calls["list_tags"] == 2
        assert fake_client.calls["list_correspondents"] == 2

    async def test_refresh_refetches_unconditionally_even_within_ttl(
        self, registry: MetadataRegistry, fake_client: _FakeClient, clock: _FakeClock
    ) -> None:
        fake_client.tags = [_tag(1, "Urgent")]
        await registry.tag_by_id(1)
        clock.advance(1.0)  # nowhere near expiry
        await registry.refresh(MetadataKind.TAG)
        assert fake_client.calls["list_tags"] == 2

    async def test_refresh_reflects_newly_added_objects(
        self, registry: MetadataRegistry, fake_client: _FakeClient
    ) -> None:
        fake_client.tags = [_tag(1, "Urgent")]
        await registry.tag_by_id(1)
        fake_client.tags = [_tag(1, "Urgent"), _tag(2, "New")]
        await registry.refresh(MetadataKind.TAG)
        assert (await registry.tag_by_id(2)).name == "New"

    async def test_stale_cache_is_never_treated_as_the_source_of_truth(
        self, registry: MetadataRegistry, fake_client: _FakeClient, clock: _FakeClock
    ) -> None:
        """After the TTL elapses, the registry reflects Paperless again -
        it never keeps serving a stale answer indefinitely.
        """
        fake_client.tags = [_tag(1, "Old Name")]
        await registry.tag_by_id(1)
        fake_client.tags = [_tag(1, "New Name")]
        clock.advance(61.0)
        tag = await registry.tag_by_id(1)
        assert tag.name == "New Name"


class TestSnapshots:
    async def test_all_tags_returns_every_cached_tag(
        self, registry: MetadataRegistry, fake_client: _FakeClient
    ) -> None:
        fake_client.tags = [_tag(1, "A"), _tag(2, "B")]
        tags = await registry.all_tags()
        assert {t.id for t in tags} == {1, 2}

    async def test_all_custom_fields_returns_every_cached_field(
        self, registry: MetadataRegistry, fake_client: _FakeClient
    ) -> None:
        fake_client.custom_fields = [_custom_field(1, "A"), _custom_field(2, "B")]
        fields = await registry.all_custom_fields()
        assert {f.id for f in fields} == {1, 2}


def _unused(_: Any) -> None:  # pragma: no cover - keeps `Any` import intentional
    pass
