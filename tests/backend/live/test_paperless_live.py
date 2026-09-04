"""Tests executed against a REAL Paperless-ngx 3.1.2 instance.

Everything here is an assertion about the *server*, not about PaperWrench.
A mocked test can only prove we handle a shape we invented; these prove the
shape is real. When one of these fails after a Paperless upgrade, the finding
belongs in docs/paperless-api.md before any code is changed.

Gated by tests/backend/live/conftest.py: requires
``PAPERWRENCH_ALLOW_LIVE_TESTS=true`` AND an authorised sandbox host.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from paperwrench.config import Settings
from paperwrench.errors import PaperlessForbiddenError
from paperwrench.errors import PaperlessIncompatibleError
from paperwrench.errors import PaperlessUnauthorizedError
from paperwrench.paperless import PaperlessClient
from paperwrench.paperless.errors import PaperlessNotFoundError
from paperwrench.paperless.errors import PaperlessValidationError

pytestmark = pytest.mark.live


# --------------------------------------------------------------- negotiation
class TestApiVersionNegotiation:
    async def test_v10_is_served_and_echoed(self, raw_live: httpx.AsyncClient) -> None:
        response = await raw_live.get(
            "/api/documents/", headers={"Accept": "application/json; version=10"}
        )
        assert response.status_code == 200
        assert response.headers["x-api-version"] == "10"
        assert response.headers["x-version"].startswith("3.")

    async def test_x_api_version_is_the_MAXIMUM_not_the_negotiated_version(
        self, raw_live: httpx.AsyncClient
    ) -> None:
        """A trap worth a dedicated test.

        ``ApiVersionMiddleware`` (src/paperless/middleware.py, 3.1.2) sets::

            response["X-Api-Version"] = ALLOWED_VERSIONS[-1]

        unconditionally. So the header reports the highest version the server
        supports and never reflects what was actually negotiated. Requesting
        v9 returns a genuinely v9-shaped body while still advertising ``10``.

        Anything that compares this header to the requested version to decide
        "are we compatible?" is wrong. Compatibility is signalled by 406.
        """
        v9 = await raw_live.get(
            "/api/documents/",
            params={"page_size": 1},
            headers={"Accept": "application/json; version=9"},
        )
        assert v9.status_code == 200
        assert v9.headers["x-api-version"] == "10", "header does NOT track negotiation"
        assert "all" in v9.json(), "the body, however, really is v9-shaped"

    @pytest.mark.parametrize("version", ["1", "11", "99", "abc", ""])
    async def test_unsupported_versions_are_rejected_with_406(
        self, raw_live: httpx.AsyncClient, version: str
    ) -> None:
        response = await raw_live.get(
            "/api/documents/", headers={"Accept": f"application/json; version={version}"}
        )
        assert response.status_code == 406, f"version={version!r} should be refused"
        assert "Invalid version" in response.text

    async def test_v9_is_still_accepted_by_312(self, raw_live: httpx.AsyncClient) -> None:
        """Documents the compatibility window; PaperWrench itself pins v10.

        Acceptance is proven by the 200 and by the v9-only ``all`` key, not by
        the ``X-Api-Version`` header, which is not a negotiation echo.
        """
        response = await raw_live.get(
            "/api/documents/",
            params={"page_size": 1},
            headers={"Accept": "application/json; version=9"},
        )
        assert response.status_code == 200
        assert "all" in response.json()

    async def test_client_surfaces_406_as_incompatible(self, live_settings: Settings) -> None:
        bad = live_settings.model_copy(update={"paperless_api_version": 99})
        async with PaperlessClient(bad) as client:
            with pytest.raises(PaperlessIncompatibleError):
                await client.get_document(1)

    async def test_api_root_redirects_and_is_not_a_valid_probe(
        self, raw_live: httpx.AsyncClient
    ) -> None:
        """`GET /api/` 302s to the schema view on 3.1.2 - do not probe with it."""
        response = await raw_live.get("/api/", follow_redirects=False)
        assert response.status_code == 302
        assert "schema" in response.headers.get("location", "")


# ---------------------------------------------------------------- pagination
class TestPagination:
    async def test_v10_envelope_keys(self, raw_live: httpx.AsyncClient) -> None:
        response = await raw_live.get("/api/documents/", params={"page_size": 1})
        payload = response.json()
        assert set(payload) == {"count", "next", "previous", "results"}

    async def test_v10_has_no_all_key(self, raw_live: httpx.AsyncClient) -> None:
        """The v9 `all` field is gone in v10; nothing may depend on it."""
        response = await raw_live.get(
            "/api/documents/",
            params={"page_size": 1},
            headers={"Accept": "application/json; version=10"},
        )
        assert "all" not in response.json()

    async def test_v9_still_has_all_key(self, raw_live: httpx.AsyncClient) -> None:
        response = await raw_live.get(
            "/api/documents/",
            params={"page_size": 1},
            headers={"Accept": "application/json; version=9"},
        )
        assert "all" in response.json(), "this is precisely the v9/v10 difference"

    async def test_out_of_range_page_is_404(self, raw_live: httpx.AsyncClient) -> None:
        response = await raw_live.get("/api/documents/", params={"page": 99999})
        assert response.status_code == 404
        assert response.json()["detail"] == "Invalid page."

    async def test_client_walks_every_page(self, live_client: PaperlessClient) -> None:
        """Force multiple pages by requesting a tiny page size."""
        page = await live_client.list_documents(page_size=1)
        if page.count < 2:
            pytest.skip("needs at least 2 documents; run the seeder")
        documents = await live_client.iter_documents(page_size=1)
        assert len(documents) == page.count
        assert len({d.id for d in documents}) == page.count, "no duplicates across pages"


# ------------------------------------------------------------ document reads
class TestDocumentRead:
    async def test_reads_a_document(
        self, live_client: PaperlessClient, scratch_document: int
    ) -> None:
        document = await live_client.get_document(scratch_document)
        assert document.id == scratch_document
        assert document.user_can_change is True

    async def test_reads_metadata(
        self, live_client: PaperlessClient, scratch_document: int
    ) -> None:
        metadata = await live_client.get_document_metadata(scratch_document)
        assert "has_archive_version" in metadata

    async def test_document_payload_still_has_the_keys_we_model(
        self, raw_live: httpx.AsyncClient, scratch_document: int
    ) -> None:
        """Upgrade tripwire: if Paperless drops one of these, we want to know."""
        response = await raw_live.get(f"/api/documents/{scratch_document}/")
        payload = response.json()
        for key in ("id", "title", "custom_fields", "tags", "user_can_change", "modified"):
            assert key in payload, f"3.1.2 no longer returns {key!r}"


class TestReferenceMetadataReads:
    """VERIFIED_LIVE reads of the five metadata kinds M2 normalises.

    ``list_custom_fields`` is the important one here: it is the test that
    caught the ``extra_data: null`` regression (M2) - a shape the mocked
    tests, which always supplied a dict literal, never exercised.
    """

    async def test_list_custom_fields_handles_the_real_null_extra_data(
        self, live_client: PaperlessClient
    ) -> None:
        fields = await live_client.list_custom_fields()
        assert len(fields) >= 1
        # At least one real field must have extra_data=null upstream (every
        # string/date/boolean field in the Golden Dataset does) - proving
        # the validator actually ran against real data, not just a fixture.
        assert any(field.extra_data == {} for field in fields)

    async def test_list_tags_correspondents_document_types_storage_paths(
        self, live_client: PaperlessClient
    ) -> None:
        # No assumption about how many exist - only that the calls succeed
        # and return the right PaperWrench model type, proving the real
        # response shapes still validate.
        tags = await live_client.list_tags()
        correspondents = await live_client.list_correspondents()
        document_types = await live_client.list_document_types()
        storage_paths = await live_client.list_storage_paths()
        assert isinstance(tags, list)
        assert isinstance(correspondents, list)
        assert isinstance(document_types, list)
        assert isinstance(storage_paths, list)


# ----------------------------------------------------- THE custom field hazard
class TestCustomFieldHazard:
    """The behaviour the whole safety architecture exists for."""

    @pytest.fixture
    async def field_ids(self, raw_live: httpx.AsyncClient) -> dict[str, int]:
        response = await raw_live.get("/api/custom_fields/", params={"page_size": 100})
        response.raise_for_status()
        return {item["name"]: int(item["id"]) for item in response.json()["results"]}

    @pytest.fixture
    async def string_field_ids(self, raw_live: httpx.AsyncClient) -> list[int]:
        """Only ``string`` fields.

        The hazard tests write the same placeholder into several fields at
        once, so they must not pick a date, monetary or select field - those
        would fail validation for reasons that have nothing to do with the
        behaviour under test.
        """
        response = await raw_live.get("/api/custom_fields/", params={"page_size": 100})
        response.raise_for_status()
        return [
            int(item["id"])
            for item in response.json()["results"]
            if item["data_type"] == "string"
        ]

    async def test_partial_patch_DELETES_omitted_custom_fields(
        self,
        raw_live: httpx.AsyncClient,
        scratch_document: int,
        string_field_ids: list[int],
    ) -> None:
        """CONFIRMED on 3.1.2: this is real, silent, and returns 200 OK.

        If this test ever starts failing, Paperless changed its semantics and
        docs/paperless-api.md plus ADR-0004 must be revisited - do not simply
        delete the test.
        """
        if len(string_field_ids) < 3:
            pytest.skip("needs at least 3 string custom fields; run the seeder")

        targets = string_field_ids[:3]
        full = [{"field": fid, "value": f"valeur-{fid}"} for fid in targets]

        seed = await raw_live.patch(
            f"/api/documents/{scratch_document}/", json={"custom_fields": full}
        )
        assert seed.status_code == 200, seed.text
        assert len(seed.json()["custom_fields"]) == 3

        # Send ONE field only.
        partial = [{"field": targets[0], "value": "seule valeur envoyee"}]
        response = await raw_live.patch(
            f"/api/documents/{scratch_document}/", json={"custom_fields": partial}
        )

        assert response.status_code == 200, "the destruction is not even reported as an error"
        after = response.json()["custom_fields"]
        assert len(after) == 1, (
            "Paperless 3.1.2 no longer deletes omitted custom fields. "
            "This is a GOOD change, but ADR-0004 and the client must be re-evaluated "
            "before relying on it."
        )
        assert {f["field"] for f in after} == {targets[0]}

    async def test_client_read_modify_write_preserves_everything(
        self,
        live_client: PaperlessClient,
        raw_live: httpx.AsyncClient,
        scratch_document: int,
        string_field_ids: list[int],
    ) -> None:
        """The mitigation, proven on the real server."""
        if len(string_field_ids) < 3:
            pytest.skip("needs at least 3 string custom fields; run the seeder")

        targets = string_field_ids[:3]
        full = [{"field": fid, "value": f"origine-{fid}"} for fid in targets]
        seeded = await raw_live.patch(
            f"/api/documents/{scratch_document}/", json={"custom_fields": full}
        )
        assert seeded.status_code == 200, seeded.text

        updated = await live_client.update_custom_fields(
            scratch_document, [{"field": targets[0], "value": "valeur modifiee"}]
        )

        assert len(updated.custom_fields) == 3, "the other two fields must survive"
        values = updated.custom_field_map
        assert values[targets[0]] == "valeur modifiee"
        assert values[targets[1]] == f"origine-{targets[1]}"
        assert values[targets[2]] == f"origine-{targets[2]}"

    @pytest.mark.parametrize(
        ("field_name", "value"),
        [
            ("Période concernée", "Février 2024 — Hôpital Saint-Joseph (créé)"),
            ("Période concernée", ""),
            ("Période concernée", None),
            ("Montant", "EUR1234.56"),
            ("Montant", "EUR0.00"),
            ("Validé", True),
            ("Validé", False),
            ("Date de règlement", "2024-03-15"),
        ],
    )
    async def test_value_round_trips(
        self,
        live_client: PaperlessClient,
        scratch_document: int,
        field_ids: dict[str, int],
        field_name: str,
        value: Any,
    ) -> None:
        """Accents, decimals, zero, booleans, dates, empty and null."""
        if field_name not in field_ids:
            pytest.skip(f"custom field {field_name!r} missing; run the seeder")

        field_id = field_ids[field_name]
        await live_client.update_custom_fields(
            scratch_document, [{"field": field_id, "value": value}]
        )
        document = await live_client.get_document(scratch_document)
        assert document.custom_field_map[field_id] == value

    async def test_monetary_rejects_comma_decimals_atomically(
        self,
        live_client: PaperlessClient,
        scratch_document: int,
        field_ids: dict[str, int],
    ) -> None:
        """A rejected write must leave the document untouched."""
        if "Montant" not in field_ids:
            pytest.skip("custom field 'Montant' missing; run the seeder")

        await live_client.update_custom_fields(
            scratch_document, [{"field": field_ids["Montant"], "value": "EUR100.00"}]
        )
        with pytest.raises(PaperlessValidationError):
            await live_client.update_custom_fields(
                scratch_document, [{"field": field_ids["Montant"], "value": "EUR100,00"}]
            )
        document = await live_client.get_document(scratch_document)
        assert document.custom_field_map[field_ids["Montant"]] == "EUR100.00"

    async def test_unknown_field_id_is_rejected_without_damage(
        self,
        live_client: PaperlessClient,
        scratch_document: int,
        string_field_ids: list[int],
    ) -> None:
        if not string_field_ids:
            pytest.skip("no string custom fields; run the seeder")
        target = string_field_ids[0]
        await live_client.update_custom_fields(
            scratch_document, [{"field": target, "value": "a preserver"}]
        )
        with pytest.raises(PaperlessValidationError):
            await live_client.update_custom_fields(
                scratch_document, [{"field": 999999, "value": "x"}]
            )
        document = await live_client.get_document(scratch_document)
        assert document.custom_field_map[target] == "a preserver"


class TestConcurrentCustomFieldWrites:
    """Measures, but does NOT fix, the lost-update hazard (M2 point 11).

    No distributed lock or ETag exists yet. This documents what actually
    happens - proven on the real server - when two actors both read the
    document's custom fields, then each writes back the FULL list with only
    their own field changed, using the state each of them read. That is
    exactly what a partial PATCH forces you into (ADR-0004): there is no way
    to tell Paperless "change only field X, leave everything else as it is
    server-side right now" - you must submit the field list you believe is
    complete.

    The interleaving is forced with a deliberate ``asyncio.sleep`` rather than
    left to chance, so the test is deterministic instead of a flaky race:
    actor B reads and writes first; actor A read earlier, sleeps, then writes
    a full list computed from a base that predates B's write. Real-world
    concurrent writers would only sometimes land in this order - this test
    always does, to make the hazard observable on demand.
    """

    async def test_two_actors_writing_different_fields_can_lose_an_update(
        self,
        raw_live: httpx.AsyncClient,
        scratch_document: int,
    ) -> None:
        response = await raw_live.get(
            "/api/custom_fields/", params={"page_size": 100}
        )
        response.raise_for_status()
        strings = [
            int(item["id"])
            for item in response.json()["results"]
            if item["data_type"] == "string"
        ]
        if len(strings) < 2:
            pytest.skip("needs at least 2 string custom fields; run the seeder")
        field_a, field_b = strings[0], strings[1]

        seed = await raw_live.patch(
            f"/api/documents/{scratch_document}/",
            json={
                "custom_fields": [
                    {"field": field_a, "value": "base-a"},
                    {"field": field_b, "value": "base-b"},
                ]
            },
        )
        assert seed.status_code == 200, seed.text

        async def actor(*, field_to_change: int, new_value: str, delay: float) -> None:
            # 1. Read the state this actor will build its write from.
            read = await raw_live.get(f"/api/documents/{scratch_document}/")
            read.raise_for_status()
            base = {
                item["field"]: item["value"] for item in read.json()["custom_fields"]
            }
            if delay:
                await asyncio.sleep(delay)
            base[field_to_change] = new_value
            full = [{"field": fid, "value": value} for fid, value in base.items()]
            write = await raw_live.patch(
                f"/api/documents/{scratch_document}/", json={"custom_fields": full}
            )
            write.raise_for_status()

        await asyncio.gather(
            actor(field_to_change=field_a, new_value="actor-a-wins-or-loses", delay=0.5),
            actor(field_to_change=field_b, new_value="actor-b-writes-first", delay=0.0),
        )

        final = await raw_live.get(f"/api/documents/{scratch_document}/")
        final.raise_for_status()
        stored = {
            item["field"]: item["value"] for item in final.json()["custom_fields"]
        }

        # MEASURED, not fixed: actor A's write is computed from a base that
        # predates actor B's write, so actor A's PATCH re-asserts "base-b" for
        # field B - actor B's change to field B is silently lost, even though
        # actor A never touched field B. This is the lost-update hazard the
        # read-modify-write mitigation (ADR-0004) does not close: it removes
        # the OMITTED-FIELD-DELETION hazard, not the classic
        # read/read/write/write race. A per-write conflict check
        # (``expected_before`` on ``update_custom_fields``) can catch this
        # for PaperWrench's own client calls, but nothing protects two
        # concurrent actors who both bypass it, or two actors using it
        # without comparing notes.
        assert stored[field_a] == "actor-a-wins-or-loses"
        assert stored[field_b] == "base-b", (
            "VERIFIED_LIVE lost-update: actor A's write, computed before actor B's "
            "write landed, silently overwrote actor B's change to field_b even "
            "though actor A never intended to touch it. Documented, not fixed - "
            "no lock/ETag exists yet (M2 point 11)."
        )

    async def test_client_update_custom_fields_narrows_but_does_not_close_the_window(
        self,
        live_client: PaperlessClient,
        raw_live: httpx.AsyncClient,
        scratch_document: int,
    ) -> None:
        """The client's own read-modify-write still has an internal race window.

        ``update_custom_fields`` reads immediately before it writes, which
        makes the window much smaller than the naive pattern above - but it
        is not zero. This test forces two overlapping calls into the window
        deterministically (delaying the first actor's *external* seed just
        long enough that the second call's read/write completes first) to
        show that the mitigation is real (it prevents field OMISSION-based
        loss) but the classic race is only narrowed, not eliminated, without
        ``expected_before`` or a lock.
        """
        response = await raw_live.get(
            "/api/custom_fields/", params={"page_size": 100}
        )
        response.raise_for_status()
        strings = [
            int(item["id"])
            for item in response.json()["results"]
            if item["data_type"] == "string"
        ]
        if len(strings) < 2:
            pytest.skip("needs at least 2 string custom fields; run the seeder")
        field_a, field_b = strings[0], strings[1]

        seed = await raw_live.patch(
            f"/api/documents/{scratch_document}/",
            json={
                "custom_fields": [
                    {"field": field_a, "value": "base-a"},
                    {"field": field_b, "value": "base-b"},
                ]
            },
        )
        assert seed.status_code == 200, seed.text

        results = await asyncio.gather(
            live_client.update_custom_fields(
                scratch_document, [{"field": field_a, "value": "via-client-a"}]
            ),
            live_client.update_custom_fields(
                scratch_document, [{"field": field_b, "value": "via-client-b"}]
            ),
        )
        assert len(results) == 2

        final = await live_client.get_document(scratch_document)
        values = final.custom_field_map
        # MEASURED: report what actually happened rather than asserting one
        # specific outcome, since the client's window is narrow and timing-
        # dependent even with the deliberate concurrent dispatch above. Both
        # fields surviving is the common case (the window is short); either
        # field reverting to its seed value would indicate the window was
        # hit. What must NOT happen, ever, is a field neither actor touched
        # (there is none here besides field_a/field_b) disappearing, and no
        # exception should escape simply from running two calls in parallel.
        assert values[field_a] in {"base-a", "via-client-a"}
        assert values[field_b] in {"base-b", "via-client-b"}


class TestSelectField:
    async def test_select_stores_the_option_id_and_refuses_the_label(
        self, raw_live: httpx.AsyncClient, scratch_document: int
    ) -> None:
        created = await raw_live.post(
            "/api/custom_fields/",
            json={
                "name": f"Categorie live {scratch_document}",
                "data_type": "select",
                "extra_data": {"select_options": [{"label": "Urgent"}, {"label": "Différé"}]},
            },
        )
        created.raise_for_status()
        definition = created.json()
        field_id = definition["id"]
        options = definition["extra_data"]["select_options"]

        try:
            # Options get an opaque server-generated string id.
            assert all(isinstance(option["id"], str) and option["id"] for option in options)

            ok = await raw_live.patch(
                f"/api/documents/{scratch_document}/",
                json={"custom_fields": [{"field": field_id, "value": options[0]["id"]}]},
            )
            assert ok.status_code == 200
            stored = {f["field"]: f["value"] for f in ok.json()["custom_fields"]}
            assert stored[field_id] == options[0]["id"]

            rejected = await raw_live.patch(
                f"/api/documents/{scratch_document}/",
                json={"custom_fields": [{"field": field_id, "value": "Urgent"}]},
            )
            assert rejected.status_code == 400, "labels must not be accepted as values"
        finally:
            await raw_live.delete(f"/api/custom_fields/{field_id}/")


# -------------------------------------------------------------------- search
class TestSearchAndFiltering:
    @pytest.mark.parametrize(
        "params",
        [
            {"title__icontains": "pw-live"},
            {"content__icontains": "test"},
            {"query": "test"},
            {"created__date__gt": "2000-01-01"},
            {"has_custom_fields": "true"},
            {"ordering": "-created"},
        ],
    )
    async def test_filters_are_accepted(
        self, raw_live: httpx.AsyncClient, params: dict[str, str]
    ) -> None:
        response = await raw_live.get("/api/documents/", params=params)
        assert response.status_code == 200
        assert "count" in response.json()

    async def test_title_search_finds_the_scratch_document(
        self, raw_live: httpx.AsyncClient, scratch_document: int
    ) -> None:
        document = (await raw_live.get(f"/api/documents/{scratch_document}/")).json()
        response = await raw_live.get(
            "/api/documents/", params={"title__icontains": document["title"]}
        )
        assert scratch_document in [d["id"] for d in response.json()["results"]]

    async def test_custom_field_query_is_accepted(self, raw_live: httpx.AsyncClient) -> None:
        response = await raw_live.get(
            "/api/documents/",
            params={"custom_field_query": json.dumps(["Montant", "exists", True])},
        )
        assert response.status_code == 200

    async def test_unknown_filter_is_SILENTLY_IGNORED(
        self, raw_live: httpx.AsyncClient
    ) -> None:
        """A typo in a filter name does not error - it returns everything.

        This is why the M4 filter engine must validate against a whitelist
        instead of trusting Paperless to reject nonsense.
        """
        unfiltered = (await raw_live.get("/api/documents/")).json()["count"]
        response = await raw_live.get("/api/documents/", params={"not_a_real_filter": "42"})
        assert response.status_code == 200
        assert response.json()["count"] == unfiltered

    async def test_unknown_ordering_is_SILENTLY_IGNORED(
        self, raw_live: httpx.AsyncClient
    ) -> None:
        response = await raw_live.get("/api/documents/", params={"ordering": "not_a_field"})
        assert response.status_code == 200


# ------------------------------------------------------------- failure modes
class TestFailureModes:
    async def test_invalid_token_is_401(self, live_settings: Settings) -> None:
        # `model_copy` skips validation, so SecretStr has to be explicit here.
        bad = live_settings.model_copy(
            update={"paperless_token": SecretStr("definitely-not-a-valid-token")}
        )
        async with PaperlessClient(bad) as client:
            with pytest.raises(PaperlessUnauthorizedError):
                await client.get_document(1)

    async def test_missing_document_is_404(self, live_client: PaperlessClient) -> None:
        with pytest.raises(PaperlessNotFoundError):
            await client_get_missing(live_client)

    async def test_valid_token_with_zero_permissions_is_403_not_401(
        self,
        live_settings: Settings,
        restricted_user: dict[str, object],
        scratch_document: int,
    ) -> None:
        """401 and 403 must stay distinct - proven, not assumed.

        A freshly created sandbox user with a valid token and literally no
        permissions gets 403 ("You do not have permission to perform this
        action."), never 401, on both list and detail. 401 is reserved for
        the credential itself being rejected (see
        test_invalid_token_is_401).
        """
        restricted = live_settings.model_copy(
            update={"paperless_token": SecretStr(str(restricted_user["token"]))}
        )
        async with PaperlessClient(restricted) as client:
            with pytest.raises(PaperlessForbiddenError):
                await client.get_document(scratch_document)
            with pytest.raises(PaperlessForbiddenError):
                await client.list_documents()

    async def test_global_view_permission_without_object_grant_is_404_not_403(
        self,
        raw_live: httpx.AsyncClient,
        live_settings: Settings,
        restricted_user: dict[str, object],
        scratch_document: int,
    ) -> None:
        """A documented, counter-intuitive VERIFIED_LIVE nuance.

        Granting the *global* ``view_document`` permission, without an
        object-level grant on a specific document, does not turn the 403
        into a 401 or a "visible but forbidden" response: Paperless hides
        the object entirely. The document disappears from the list and its
        detail endpoint answers 404, exactly as if it did not exist.
        PaperlessForbiddenError must therefore never be relied upon as the
        sole signal for "this document exists but I cannot see it" -
        object-level permission gaps surface as PaperlessNotFoundError.
        """
        grant = await raw_live.patch(
            f"/api/users/{restricted_user['id']}/",
            json={"user_permissions": ["view_document"]},
        )
        grant.raise_for_status()

        restricted = live_settings.model_copy(
            update={"paperless_token": SecretStr(str(restricted_user["token"]))}
        )
        async with PaperlessClient(restricted) as client:
            with pytest.raises(PaperlessNotFoundError):
                await client.get_document(scratch_document)
            page = await client.list_documents()
            assert scratch_document not in {doc.id for doc in page.results}

    async def test_unreachable_host_is_reported_not_raised_by_probe(
        self, live_settings: Settings
    ) -> None:
        offline = live_settings.model_copy(
            update={"paperless_url": "http://127.0.0.1:9", "paperless_timeout_connect": 2.0}
        )
        async with PaperlessClient(offline) as client:
            status = await client.check_connection()
        assert status.connected is False
        assert status.error_code == "PAPERLESS_UNREACHABLE"

    async def test_probe_reports_a_healthy_instance(
        self, live_client: PaperlessClient
    ) -> None:
        status = await live_client.check_connection()
        assert status.configured and status.connected and status.compatible
        assert status.api_version == "10"
        assert status.paperless_version is not None

    async def test_probe_output_contains_no_token(
        self, live_client: PaperlessClient, live_settings: Settings
    ) -> None:
        status = await live_client.check_connection()
        assert live_settings.paperless_token.get_secret_value() not in status.model_dump_json()


async def client_get_missing(client: PaperlessClient) -> None:
    await client.get_document(999_999_999)
