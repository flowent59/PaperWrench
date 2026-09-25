"""M5 contract on the guarded, disposable Paperless sandbox."""

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio
from pydantic import SecretStr

from paperwrench.config import Settings
from paperwrench.errors import PaperlessForbiddenError
from paperwrench.errors import PaperlessUnauthorizedError
from paperwrench.main import create_app
from paperwrench.paperless import MetadataRegistry
from paperwrench.paperless import PaperlessClient
from paperwrench.paperless.errors import PaperlessConflictError
from paperwrench.paperless.errors import PaperlessNotFoundError
from paperwrench.paperless.models import merge_custom_fields
from paperwrench.paperless.mutations import revision

pytestmark = pytest.mark.live


async def test_core_allowlist_references_date_asn_and_null_live(
    inspector_api: httpx.AsyncClient,
    raw_live: httpx.AsyncClient,
    scratch_document: int,
) -> None:
    created: list[tuple[str, int]] = []
    values: dict[str, Any] = {
        "title": "M5 core",
        "created": "2024-02-29",
        "archive_serial_number": 0,
    }
    try:
        for endpoint, key, extra in [
            ("tags", "tags", {}),
            ("correspondents", "correspondent", {}),
            ("document_types", "document_type", {}),
            ("storage_paths", "storage_path", {"path": "m5-test/{title}"}),
        ]:
            response = await raw_live.post(
                f"/api/{endpoint}/",
                json={
                    "name": f"M5 {endpoint} {scratch_document}",
                    "matching_algorithm": 0,
                    **extra,
                },
            )
            response.raise_for_status()
            object_id = int(response.json()["id"])
            created.append((endpoint, object_id))
            values[key] = [object_id] if key == "tags" else object_id
        path = f"/api/v1/documents/{scratch_document}"
        for payload in [
            values,
            {
                "correspondent": None,
                "document_type": None,
                "storage_path": None,
                "tags": [],
                "archive_serial_number": None,
            },
        ]:
            detail = (await inspector_api.get(path)).json()
            response = await inspector_api.patch(
                path,
                json={
                    "expected_revision": detail["revision"],
                    "catalog_revision": detail["catalog_revision"],
                    "core": payload,
                },
            )
            assert response.status_code == 200, response.text
            raw = await raw_live.get(f"/api/documents/{scratch_document}/")
            raw.raise_for_status()
            for key, value in payload.items():
                assert raw.json()[key] == value
    finally:
        for endpoint, object_id in reversed(created):
            response = await raw_live.delete(f"/api/{endpoint}/{object_id}/")
            response.raise_for_status()


@pytest_asyncio.fixture
async def inspector_api(
    live_settings: Settings,
    live_client: PaperlessClient,
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(live_settings)
    app.state.paperless_client = live_client
    app.state.metadata_registry = MetadataRegistry(live_client)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as api:
        yield api


async def test_inspector_acceptance_normalization_and_states(
    inspector_api: httpx.AsyncClient,
    raw_live: httpx.AsyncClient,
    scratch_document: int,
    golden_custom_field_ids: dict[str, int],
) -> None:
    ids = golden_custom_field_ids
    period, amount = ids["Période concernée"], ids["Montant"]
    upstream = f"/api/documents/{scratch_document}/"
    types = await raw_live.get("/api/document_types/")
    types.raise_for_status()
    doc_type = next(t["id"] for t in types.json()["results"] if t["name"] == "Relevé de vacations")
    seeded = await raw_live.patch(
        upstream,
        json={
            "document_type": doc_type,
            "custom_fields": [
                {"field": period, "value": "Janvier"},
                {"field": amount, "value": "EUR0.00"},
                {"field": ids["Validé"], "value": False},
                {"field": ids["Commentaire"], "value": None},
                {"field": ids["Référence interne"], "value": ""},
            ],
        },
    )
    seeded.raise_for_status()
    select = await raw_live.post(
        "/api/custom_fields/",
        json={
            "name": f"M5 select {scratch_document}",
            "data_type": "select",
            "extra_data": {"select_options": [{"label": "Urgent"}, {"label": "Différé"}]},
        },
    )
    select.raise_for_status()
    select_id = select.json()["id"]
    option_id = select.json()["extra_data"]["select_options"][1]["id"]
    path = f"/api/v1/documents/{scratch_document}"
    try:
        detail = (await inspector_api.get(path)).json()
        assert detail["document_type"]["name"] == "Relevé de vacations"
        response = await inspector_api.patch(
            path,
            json={
                "expected_revision": detail["revision"],
                "catalog_revision": detail["catalog_revision"],
                "core": {"title": "   Relevé de vacations — février   "},
                "custom_changes": [
                    {"field_id": period, "kind": "present", "value": "Février — été"}
                ],
                "acknowledge_external_race": True,
            },
        )
        assert response.status_code == 200, response.text
        receipt = response.json()
        assert receipt["intended"]["title"].startswith("   ")
        assert receipt["document"]["title"] == "Relevé de vacations — février"
        stored = (await raw_live.get(upstream)).json()
        before_untouched = {
            v["field"]: v["value"] for v in seeded.json()["custom_fields"] if v["field"] != period
        }
        after = {v["field"]: v["value"] for v in stored["custom_fields"]}
        assert after[period] == "Février — été"
        assert {key: after[key] for key in before_untouched} == before_untouched

        # Stale revision, including competing edits to a different field: zero write.
        stale = await inspector_api.patch(
            path,
            json={
                "expected_revision": detail["revision"],
                "catalog_revision": detail["catalog_revision"],
                "core": {"title": "must not land"},
            },
        )
        assert stale.status_code == 409
        assert (await raw_live.get(upstream)).json() == stored

        for changes in [
            [
                {"field_id": amount, "kind": "present", "value": "EUR1234.56"},
                {"field_id": select_id, "kind": "present", "value": option_id},
            ],
            [{"field_id": period, "kind": "present", "value": ""}],
            [{"field_id": period, "kind": "null"}],
            [{"field_id": period, "kind": "absent"}],
        ]:
            detail = (await inspector_api.get(path)).json()
            result = await inspector_api.patch(
                path,
                json={
                    "expected_revision": detail["revision"],
                    "catalog_revision": detail["catalog_revision"],
                    "custom_changes": changes,
                    "acknowledge_external_race": True,
                },
            )
            assert result.status_code == 200, result.text
            values = {v["field_id"]: v for v in result.json()["document"]["custom_fields"]}
            for change in changes:
                assert values[change["field_id"]]["kind"] == change["kind"]
                assert values[change["field_id"]]["raw"] == change.get("value")
        final = (await raw_live.get(upstream)).json()
        values = {v["field"]: v["value"] for v in final["custom_fields"]}
        assert values[amount] == "EUR1234.56" and values[select_id] == option_id
        assert values[ids["Validé"]] is False
    finally:
        deleted = await raw_live.delete(f"/api/custom_fields/{select_id}/")
        deleted.raise_for_status()


async def test_cooperating_writers_are_serialized_then_conflicted_live(
    live_client: PaperlessClient,
    scratch_document: int,
    golden_custom_field_ids: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    field = golden_custom_field_ids["Période concernée"]
    original = await live_client.get_document(scratch_document)
    entered, release = asyncio.Event(), asyncio.Event()
    request = live_client._request
    reads = 0
    patches = 0

    async def intercepted(method: str, path: str, **kwargs: Any) -> httpx.Response:
        nonlocal reads, patches
        if method == "GET":
            reads += 1
            response = await request(method, path, **kwargs)
            if reads == 1:
                entered.set()
                await release.wait()
            return response
        patches += 1
        return await request(method, path, **kwargs)

    monkeypatch.setattr(live_client, "_request", intercepted)

    async def write(value: str) -> None:
        await live_client.mutate_document(
            scratch_document,
            expected_revision=revision(original),
            core={},
            custom_updates=[{"field": field, "value": value}],
            remove_custom_fields=[],
            acknowledge_external_race=True,
        )

    first = asyncio.create_task(write("actor A"))
    await asyncio.wait_for(entered.wait(), 10)
    second = asyncio.create_task(write("actor B"))
    await asyncio.sleep(0)
    assert reads == 1
    release.set()
    await first
    with pytest.raises(PaperlessConflictError):
        await second
    assert patches == 1 and reads == 2
    assert (await live_client.get_document(scratch_document)).custom_field_map[field] == "actor A"


async def test_external_writer_race_and_if_match_are_not_atomic_live(
    live_client: PaperlessClient,
    raw_live: httpx.AsyncClient,
    scratch_document: int,
    golden_custom_field_ids: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    a, b = (
        golden_custom_field_ids["Période concernée"],
        golden_custom_field_ids["Référence interne"],
    )
    path = f"/api/documents/{scratch_document}/"
    seed = await raw_live.patch(
        path,
        json={
            "custom_fields": [
                {"field": a, "value": "base A"},
                {"field": b, "value": "base B"},
            ]
        },
    )
    seed.raise_for_status()
    # Deliberately impossible preconditions still do not prevent this write.
    probe = await raw_live.patch(
        path,
        headers={
            "If-Match": '"impossible-etag-m5"',
            "If-Unmodified-Since": "Thu, 01 Jan 1970 00:00:00 GMT",
        },
        json={"title": "Conditional headers ignored"},
    )
    assert probe.status_code == 200, probe.text
    assert probe.json()["title"] == "Conditional headers ignored"
    original = await live_client.get_document(scratch_document)
    request = live_client._request
    interleaved = False

    async def intercepted(method: str, url: str, **kwargs: Any) -> httpx.Response:
        nonlocal interleaved
        if method == "PATCH" and not interleaved:
            interleaved = True
            external = await raw_live.patch(
                path,
                json={
                    "custom_fields": merge_custom_fields(
                        original.custom_fields,
                        [{"field": b, "value": "external B"}],
                    )
                },
            )
            external.raise_for_status()
            assert {v["field"]: v["value"] for v in external.json()["custom_fields"]}[
                b
            ] == "external B"
        return await request(method, url, **kwargs)

    monkeypatch.setattr(live_client, "_request", intercepted)
    await live_client.mutate_document(
        scratch_document,
        expected_revision=revision(original),
        core={},
        custom_updates=[{"field": a, "value": "local A"}],
        remove_custom_fields=[],
        acknowledge_external_race=True,
    )
    final = (await raw_live.get(path)).json()
    values = {v["field"]: v["value"] for v in final["custom_fields"]}
    assert values[a] == "local A"
    assert values[b] == "base B", "External update is LOST: never claim global atomicity"


async def test_permission_preflight_and_hidden_document_live(
    raw_live: httpx.AsyncClient,
    live_settings: Settings,
    scratch_document: int,
    restricted_user: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = live_settings.model_copy(
        update={"paperless_token": SecretStr(str(restricted_user["token"]))}
    )
    path = f"/api/documents/{scratch_document}/"
    async with PaperlessClient(settings) as restricted:
        with pytest.raises(PaperlessForbiddenError):
            await restricted.get_document(scratch_document)
        grant = await raw_live.patch(
            f"/api/users/{restricted_user['id']}/", json={"user_permissions": ["view_document"]}
        )
        grant.raise_for_status()
        with pytest.raises(PaperlessNotFoundError):
            await restricted.get_document(scratch_document)
        shared = await raw_live.patch(
            path,
            json={
                "set_permissions": {
                    "view": {"users": [restricted_user["id"]], "groups": []},
                    "change": {"users": [], "groups": []},
                }
            },
        )
        shared.raise_for_status()
        document = await restricted.get_document(scratch_document)
        assert document.user_can_change is False
        request = restricted._request
        patches = 0

        async def intercepted(method: str, url: str, **kwargs: Any) -> httpx.Response:
            nonlocal patches
            patches += method == "PATCH"
            return await request(method, url, **kwargs)

        monkeypatch.setattr(restricted, "_request", intercepted)
        with pytest.raises(PaperlessForbiddenError):
            await restricted.mutate_document(
                scratch_document,
                expected_revision=revision(document),
                core={"title": "forbidden"},
                custom_updates=[],
                remove_custom_fields=[],
            )
        assert patches == 0
    bad = settings.model_copy(update={"paperless_token": SecretStr("invalid-m5-token")})
    async with PaperlessClient(bad) as invalid:
        with pytest.raises(PaperlessUnauthorizedError):
            await invalid.get_document(scratch_document)
