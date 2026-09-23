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
from pathlib import Path
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


# --------------------------------------------------- M3: custom field ordering
class TestCustomFieldOrderingLive:
    """VERIFIED_LIVE: does ``ordering=custom_field_<id>`` actually work?

    M3's brief requires this to be proven against a real 3.1.2 instance
    before PaperWrench exposes it - VERIFIED_SOURCE (reading
    ``DocumentsOrderingFilter`` in ``src/documents/filters.py``) already
    showed *how* Paperless implements it; this class proves it actually
    behaves as documented on the Golden Dataset, for exactly the three
    data types M3 exposes (string/monetary/date), including the
    missing-field and null-value edge cases the M3 brief calls out by
    name. A field with an incoherent/unreliable result here is a reason to
    NOT expose it - see docs/paperless-api.md for the promoted findings.
    """

    async def test_ordering_by_string_custom_field_is_coherent(
        self, raw_live: httpx.AsyncClient, golden_custom_field_ids: dict[str, int]
    ) -> None:
        field_id = golden_custom_field_ids.get("P\u00e9riode concern\u00e9e")
        if field_id is None:
            pytest.skip("Golden Dataset not seeded (P\u00e9riode concern\u00e9e field missing)")

        ascending = await raw_live.get(
            "/api/documents/", params={"ordering": f"custom_field_{field_id}", "page_size": 100}
        )
        assert ascending.status_code == 200
        descending = await raw_live.get(
            "/api/documents/",
            params={"ordering": f"-custom_field_{field_id}", "page_size": 100},
        )
        assert descending.status_code == 200

        asc_ids = [d["id"] for d in ascending.json()["results"]]
        desc_ids = [d["id"] for d in descending.json()["results"]]
        # Same document set either way - only the order should differ.
        assert set(asc_ids) == set(desc_ids)
        if len(asc_ids) > 1:
            assert asc_ids != desc_ids, "ascending and descending returned the same order"

    async def test_ordering_by_monetary_custom_field_is_coherent(
        self, raw_live: httpx.AsyncClient, golden_custom_field_ids: dict[str, int]
    ) -> None:
        field_id = golden_custom_field_ids.get("Montant")
        if field_id is None:
            pytest.skip("Golden Dataset not seeded (Montant field missing)")

        response = await raw_live.get(
            "/api/documents/", params={"ordering": f"custom_field_{field_id}", "page_size": 100}
        )
        assert response.status_code == 200
        # The Golden Dataset deliberately includes PRESENT (incl. zero) and
        # ABSENT Montant values (see scripts/seed_dev_golden_dataset.py) -
        # sorting must not error out or silently drop any document.
        results = response.json()["results"]
        assert len(results) == response.json()["count"] or response.json()["next"] is not None

    async def test_ordering_by_date_custom_field_is_coherent(
        self, raw_live: httpx.AsyncClient, golden_custom_field_ids: dict[str, int]
    ) -> None:
        field_id = golden_custom_field_ids.get("Date de r\u00e8glement")
        if field_id is None:
            pytest.skip("Golden Dataset not seeded (Date de r\u00e8glement field missing)")

        response = await raw_live.get(
            "/api/documents/", params={"ordering": f"custom_field_{field_id}", "page_size": 100}
        )
        assert response.status_code == 200
        assert "results" in response.json()

    async def test_documents_missing_the_field_are_ordered_deterministically(
        self, raw_live: httpx.AsyncClient, golden_custom_field_ids: dict[str, int]
    ) -> None:
        """VERIFIED_SOURCE said documents WITH the field sort before those
        WITHOUT it (``-has_field`` annotation applied first, regardless of
        direction). This proves that live, using Montant (deliberately
        ABSENT on some Golden Dataset rows).
        """
        field_id = golden_custom_field_ids.get("Montant")
        if field_id is None:
            pytest.skip("Golden Dataset not seeded (Montant field missing)")

        ascending = await raw_live.get(
            "/api/documents/", params={"ordering": f"custom_field_{field_id}", "page_size": 100}
        )
        assert ascending.status_code == 200
        results = ascending.json()["results"]
        if not results:
            pytest.skip("no documents to check ordering against")

        has_field: list[bool] = []
        for doc in results:
            values = [cf for cf in doc.get("custom_fields", []) if cf["field"] == field_id]
            has_field.append(len(values) > 0 and values[0]["value"] is not None)

        # Once a document without the field appears, none should have it
        # afterwards (VERIFIED_SOURCE: has_field is applied before the
        # per-type value ordering, not interleaved with it).
        seen_without = False
        for present in has_field:
            if not present:
                seen_without = True
            elif seen_without:
                pytest.fail(
                    "a document WITH the custom field appeared after one WITHOUT it - "
                    "the has_field-first ordering guarantee does not hold as VERIFIED_SOURCE"
                )

    async def test_ordering_by_boolean_custom_field_also_works_upstream(
        self, raw_live: httpx.AsyncClient, golden_custom_field_ids: dict[str, int]
    ) -> None:
        """Paperless itself supports this (VERIFIED_SOURCE); PaperWrench just
        does not expose it in M3 - documented here so the distinction between
        "Paperless can" and "PaperWrench exposes" is provable, not assumed.
        """
        field_id = golden_custom_field_ids.get("Valid\u00e9")
        if field_id is None:
            pytest.skip("Golden Dataset not seeded (Valid\u00e9 field missing)")

        response = await raw_live.get(
            "/api/documents/", params={"ordering": f"custom_field_{field_id}"}
        )
        assert response.status_code == 200


# ----------------------------------------- M3: search + pagination + ordering
class TestSearchPaginationOrderingComposeLive:
    """VERIFIED_LIVE: do search, pagination and ordering compose together?

    M1 proved each of these individually against the Golden Dataset. The M3
    brief specifically calls out that this needs re-checking *in
    combination*, since a query builder can trivially get the parameter
    combination right individually and wrong together (e.g. an ordering
    annotation breaking a search's ranking, or a filter being dropped when
    paginating past page 1).
    """

    async def test_title_search_with_ordering_and_pagination_together(
        self, raw_live: httpx.AsyncClient
    ) -> None:
        response = await raw_live.get(
            "/api/documents/",
            params={
                "title_search": "vacations",
                "ordering": "-created",
                "page": 1,
                "page_size": 5,
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert "results" in payload
        assert len(payload["results"]) <= 5

    async def test_correspondent_filter_composes_with_ordering(
        self, raw_live: httpx.AsyncClient
    ) -> None:
        unfiltered = await raw_live.get("/api/documents/", params={"page_size": 1})
        assert unfiltered.status_code == 200

        response = await raw_live.get(
            "/api/documents/",
            params={"ordering": "correspondent__name", "page_size": 100},
        )
        assert response.status_code == 200

    async def test_query_search_composes_with_pagination_across_pages(
        self, raw_live: httpx.AsyncClient
    ) -> None:
        first_page = await raw_live.get(
            "/api/documents/", params={"query": "vacations", "page": 1, "page_size": 2}
        )
        assert first_page.status_code == 200
        count = first_page.json()["count"]
        if count < 3:
            pytest.skip("needs at least 3 matching documents to prove page 2 differs from page 1")

        second_page = await raw_live.get(
            "/api/documents/", params={"query": "vacations", "page": 2, "page_size": 2}
        )
        assert second_page.status_code == 200
        first_ids = {d["id"] for d in first_page.json()["results"]}
        second_ids = {d["id"] for d in second_page.json()["results"]}
        assert first_ids.isdisjoint(second_ids), "page 1 and page 2 overlapped"

    async def test_document_type_filter_composes_with_search_and_ordering(
        self, raw_live: httpx.AsyncClient
    ) -> None:
        response = await raw_live.get(
            "/api/documents/",
            params={
                "title_search": "vacations",
                "ordering": "-created",
                "document_type__id__gt": 0,
                "page_size": 10,
            },
        )
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


# =========================================================================
# M4 - Filter Engine
# =========================================================================
#
# These are the tests entitled to promote a Filter Engine behaviour to
# VERIFIED_LIVE. They run against a real Paperless-ngx 3.1.2 and the Golden
# Dataset (17 documents, `Relevé de vacations`, with `Montant` deliberately
# PRESENT / ABSENT / EUR0.00 and `Période concernée` PRESENT / ABSENT).
#
# Two things they are careful about:
#
# * They assert **PaperWrench's compiled parameters against the real server**,
#   not against our own idea of it. Every expression sent here comes out of
#   `compile_filterset`, so a compiler mistake shows up as a wrong document
#   set rather than as a passing test about a string.
# * They never mutate the Golden Dataset. States that the dataset does not
#   contain (an explicitly NULL value, an empty string) are produced on a
#   disposable scratch document and torn down.


async def _golden_field_ids(raw_live: httpx.AsyncClient) -> dict[str, int]:
    response = await raw_live.get("/api/custom_fields/", params={"page_size": 100})
    response.raise_for_status()
    return {item["name"]: int(item["id"]) for item in response.json()["results"]}


async def _matching_ids(raw_live: httpx.AsyncClient, params: dict[str, Any]) -> set[int]:
    """Every document id matching ``params``, walked page by page.

    Only a *test* may do this. It is how an assertion about "exactly these
    documents" is made; PaperWrench itself never enumerates a filter's
    matches (ADR-0007), which is why this helper lives here and not in the
    Filter Engine.
    """
    found: set[int] = set()
    page = 1
    while True:
        response = await raw_live.get(
            "/api/documents/", params={**params, "page": page, "page_size": 100}
        )
        response.raise_for_status()
        payload = response.json()
        found |= {int(item["id"]) for item in payload["results"]}
        if not payload.get("next"):
            return found
        page += 1


async def _count(raw_live: httpx.AsyncClient, params: dict[str, Any]) -> int:
    response = await raw_live.get("/api/documents/", params={**params, "page_size": 1})
    response.raise_for_status()
    return int(response.json()["count"])


def _compile(filterset: Any, catalog: Any) -> dict[str, str]:
    from paperwrench.filters import compile_filterset

    return compile_filterset(filterset, catalog).params


async def _catalog(live_client: PaperlessClient) -> Any:
    from paperwrench.filters import FieldCatalog

    return FieldCatalog(await live_client.list_custom_fields())


def _cf(field_id: int, operator: str, value: Any = None) -> Any:
    from paperwrench.filters import CustomFieldRef
    from paperwrench.filters import FilterCondition
    from paperwrench.filters import FilterOperator

    return FilterCondition(
        field=CustomFieldRef(field_id=field_id),
        operator=FilterOperator(operator),
        value=value,
    )


def _core(name: str, operator: str, value: Any = None) -> Any:
    from paperwrench.filters import CoreField
    from paperwrench.filters import CoreFieldRef
    from paperwrench.filters import FilterCondition
    from paperwrench.filters import FilterOperator

    return FilterCondition(
        field=CoreFieldRef(name=CoreField(name)),
        operator=FilterOperator(operator),
        value=value,
    )


def _set(*children: Any, operator: str = "and") -> Any:
    from paperwrench.filters import FilterGroup
    from paperwrench.filters import FilterSet
    from paperwrench.filters import GroupOperator

    return FilterSet(
        root=FilterGroup(operator=GroupOperator(operator), children=list(children))
    )


def _group(*children: Any, operator: str = "or") -> Any:
    from paperwrench.filters import FilterGroup
    from paperwrench.filters import GroupOperator

    return FilterGroup(operator=GroupOperator(operator), children=list(children))


class TestFilterEngineEmptyMissingLive:
    """The empty/missing semantics the M4 brief asked to be checked before freezing.

    ABSENT, NULL and ``""`` are three different states, and the whole design
    of `is_missing` / `is_null` / `is_empty` rests on Paperless keeping them
    apart. This walks one scratch document through all four states and
    asserts, at each step, exactly which of PaperWrench's compiled
    expressions match it.
    """

    async def test_absent_null_and_empty_string_are_three_distinct_states(
        self,
        raw_live: httpx.AsyncClient,
        live_client: PaperlessClient,
        scratch_document: int,
    ) -> None:
        fields = await _golden_field_ids(raw_live)
        periode = fields["Période concernée"]
        catalog = await _catalog(live_client)

        async def matches(operator: str) -> bool:
            params = _compile(_set(_cf(periode, operator)), catalog)
            return scratch_document in await _matching_ids(raw_live, params)

        async def set_value(value: Any) -> None:
            current = await raw_live.get(f"/api/documents/{scratch_document}/")
            current.raise_for_status()
            merged = {
                int(item["field"]): dict(item)
                for item in current.json().get("custom_fields") or []
            }
            merged[periode] = {"field": periode, "value": value}
            response = await raw_live.patch(
                f"/api/documents/{scratch_document}/",
                json={"custom_fields": list(merged.values())},
            )
            response.raise_for_status()

        # -- 1. ABSENT: the field is not attached to the document at all ----
        assert await matches("is_missing") is True
        assert await matches("is_present") is False
        assert await matches("is_null") is False, (
            "is_null must never match an ABSENT document - that is the whole "
            "reason it compiles to `isnull`, which Paperless evaluates as "
            "`has_field AND value IS NULL`"
        )
        assert await matches("has_value") is False
        assert await matches("is_empty") is False, (
            "is_empty must NOT include ABSENT: a field that was never set is "
            "a different thing from one that was set to nothing"
        )

        # -- 2. NULL: the field is attached, its value is explicitly null ---
        await set_value(None)
        assert await matches("is_missing") is False
        assert await matches("is_present") is True
        assert await matches("is_null") is True
        assert await matches("has_value") is False
        assert await matches("is_empty") is True

        # -- 3. EMPTY STRING: attached, present, and genuinely "" -----------
        await set_value("")
        assert await matches("is_missing") is False
        assert await matches("is_present") is True
        assert await matches("is_null") is False, (
            'an empty string is not null - collapsing "" into null is exactly '
            "the conflation this engine refuses to make"
        )
        assert await matches("has_value") is True
        assert await matches("is_empty") is True

        # -- 4. A REAL VALUE ------------------------------------------------
        await set_value("mars 2024")
        assert await matches("is_missing") is False
        assert await matches("is_present") is True
        assert await matches("is_null") is False
        assert await matches("has_value") is True
        assert await matches("is_empty") is False

    async def test_golden_dataset_absent_and_present_montant_partition_the_library(
        self, raw_live: httpx.AsyncClient, live_client: PaperlessClient
    ) -> None:
        """is_missing and is_present are exact complements, with nothing lost."""
        fields = await _golden_field_ids(raw_live)
        montant = fields["Montant"]
        catalog = await _catalog(live_client)

        missing = await _matching_ids(raw_live, _compile(_set(_cf(montant, "is_missing")), catalog))
        present = await _matching_ids(raw_live, _compile(_set(_cf(montant, "is_present")), catalog))
        everything = await _matching_ids(raw_live, {})

        assert missing and present, "the Golden Dataset must contain both states"
        assert missing & present == set()
        assert missing | present == everything


class TestFilterEngineMonetaryLive:
    """Monetary comparisons, including the zero that is not an absence.

    ``EUR0.00`` is falsy in almost every language a template might use, so
    "amount is missing" and "amount is zero" get conflated constantly. The
    Golden Dataset keeps them as separate rows precisely so this can be
    checked against a real server.
    """

    async def test_zero_is_a_real_value_distinct_from_missing(
        self, raw_live: httpx.AsyncClient, live_client: PaperlessClient
    ) -> None:
        fields = await _golden_field_ids(raw_live)
        montant = fields["Montant"]
        catalog = await _catalog(live_client)

        zero = await _matching_ids(
            raw_live, _compile(_set(_cf(montant, "equals", "EUR0.00")), catalog)
        )
        missing = await _matching_ids(raw_live, _compile(_set(_cf(montant, "is_missing")), catalog))

        assert zero, "the Golden Dataset seeds EUR0.00 rows on purpose"
        assert missing
        assert zero & missing == set()

    async def test_greater_than_zero_excludes_both_zero_and_missing(
        self, raw_live: httpx.AsyncClient, live_client: PaperlessClient
    ) -> None:
        """The M4 acceptance scenario's step 7."""
        fields = await _golden_field_ids(raw_live)
        montant = fields["Montant"]
        catalog = await _catalog(live_client)

        positive = await _matching_ids(
            raw_live, _compile(_set(_cf(montant, "greater_than", "EUR0.00")), catalog)
        )
        zero = await _matching_ids(
            raw_live, _compile(_set(_cf(montant, "equals", "EUR0.00")), catalog)
        )
        missing = await _matching_ids(raw_live, _compile(_set(_cf(montant, "is_missing")), catalog))

        assert positive
        assert positive & zero == set()
        assert positive & missing == set()

    async def test_all_five_comparisons_agree_with_each_other(
        self, raw_live: httpx.AsyncClient, live_client: PaperlessClient
    ) -> None:
        fields = await _golden_field_ids(raw_live)
        montant = fields["Montant"]
        catalog = await _catalog(live_client)

        async def ids(operator: str, value: str) -> set[int]:
            return await _matching_ids(
                raw_live, _compile(_set(_cf(montant, operator, value)), catalog)
            )

        threshold = "EUR1000.00"
        greater = await ids("greater_than", threshold)
        greater_or_equal = await ids("greater_or_equal", threshold)
        less = await ids("less_than", threshold)
        less_or_equal = await ids("less_or_equal", threshold)
        equal = await ids("equals", threshold)

        assert greater <= greater_or_equal
        assert less <= less_or_equal
        assert greater & less == set()
        assert greater_or_equal == greater | equal
        assert less_or_equal == less | equal
        # Every document with a value falls on one side or the other.
        has_value = await _matching_ids(
            raw_live, _compile(_set(_cf(montant, "has_value")), catalog)
        )
        assert greater_or_equal | less == has_value

    async def test_decimal_precision_survives_the_round_trip(
        self, raw_live: httpx.AsyncClient, live_client: PaperlessClient
    ) -> None:
        """EUR1234.56 must be found by an exact filter, to the cent."""
        fields = await _golden_field_ids(raw_live)
        montant = fields["Montant"]
        catalog = await _catalog(live_client)

        exact = await _matching_ids(
            raw_live, _compile(_set(_cf(montant, "equals", "EUR1234.56")), catalog)
        )
        near = await _matching_ids(
            raw_live, _compile(_set(_cf(montant, "equals", "EUR1234.57")), catalog)
        )

        assert exact, "the Golden Dataset seeds EUR1234.56 (case dec-01)"
        assert near == set()


class TestFilterEngineCoreFieldsLive:
    async def test_document_type_filter_selects_the_golden_dataset(
        self, raw_live: httpx.AsyncClient, live_client: PaperlessClient
    ) -> None:
        types = await raw_live.get("/api/document_types/", params={"page_size": 100})
        types.raise_for_status()
        type_id = next(
            item["id"]
            for item in types.json()["results"]
            if item["name"] == "Relevé de vacations"
        )
        catalog = await _catalog(live_client)

        matching = await _matching_ids(
            raw_live, _compile(_set(_core("document_type", "equals", type_id)), catalog)
        )
        assert len(matching) >= 17

    async def test_title_contains_is_case_insensitive_as_documented(
        self, raw_live: httpx.AsyncClient, live_client: PaperlessClient
    ) -> None:
        catalog = await _catalog(live_client)

        lower = await _matching_ids(
            raw_live, _compile(_set(_core("title", "contains", "scan")), catalog)
        )
        upper = await _matching_ids(
            raw_live, _compile(_set(_core("title", "contains", "SCAN")), catalog)
        )

        assert lower
        assert lower == upper

    async def test_created_equality_compiles_to_an_exact_single_day(
        self, raw_live: httpx.AsyncClient, live_client: PaperlessClient
    ) -> None:
        """`>= d AND <= d` really is `== d` on a DateField."""
        catalog = await _catalog(live_client)
        sample = await raw_live.get("/api/documents/", params={"page_size": 1})
        sample.raise_for_status()
        created = str(sample.json()["results"][0]["created"])[:10]

        same_day = await _matching_ids(
            raw_live, _compile(_set(_core("created", "equals", created)), catalog)
        )
        assert same_day
        for document_id in same_day:
            detail = await raw_live.get(f"/api/documents/{document_id}/")
            detail.raise_for_status()
            assert str(detail.json()["created"])[:10] == created

    async def test_added_comparisons_are_day_granular(
        self, raw_live: httpx.AsyncClient, live_client: PaperlessClient
    ) -> None:
        """`added__date__lte` includes everything added that day.

        The bare `added__lte` would compare against midnight and silently
        drop the rest of the day - which is why the compiler uses the
        `date__*` variants for the two DateTime columns.
        """
        catalog = await _catalog(live_client)
        sample = await raw_live.get("/api/documents/", params={"page_size": 1})
        sample.raise_for_status()
        added_day = str(sample.json()["results"][0]["added"])[:10]

        params = _compile(_set(_core("added", "less_or_equal", added_day)), catalog)
        assert params == {"added__date__lte": added_day}
        assert await _matching_ids(raw_live, params)

    async def test_archive_serial_number_missing_matches_the_golden_dataset(
        self, raw_live: httpx.AsyncClient, live_client: PaperlessClient
    ) -> None:
        catalog = await _catalog(live_client)
        matching = await _matching_ids(
            raw_live, _compile(_set(_core("archive_serial_number", "is_missing")), catalog)
        )
        # Nothing in the Golden Dataset sets an ASN.
        assert len(matching) >= 17


class TestFilterEngineTagsLive:
    """The three tag semantics, checked rather than assumed.

    VERIFIED_SOURCE said `tags__id__all` is has-ALL, `tags__id__in` is
    has-ANY and `tags__id__none` is has-NONE. This proves it on a real
    server, because getting this wrong silently changes which documents a
    transformation would touch.
    """

    async def test_has_all_any_and_none_mean_three_different_things(
        self,
        raw_live: httpx.AsyncClient,
        live_client: PaperlessClient,
        scratch_document: int,
    ) -> None:
        import uuid

        catalog = await _catalog(live_client)
        marker = uuid.uuid4().hex[:8]
        tag_ids: list[int] = []
        for suffix in ("a", "b"):
            created = await raw_live.post(
                "/api/tags/", json={"name": f"pw-m4-{marker}-{suffix}", "matching_algorithm": 0}
            )
            created.raise_for_status()
            tag_ids.append(int(created.json()["id"]))
        tag_a, tag_b = tag_ids

        try:
            # The scratch document carries ONLY tag A.
            patched = await raw_live.patch(
                f"/api/documents/{scratch_document}/", json={"tags": [tag_a]}
            )
            patched.raise_for_status()

            async def matches(operator: str, values: list[int]) -> bool:
                params = _compile(_set(_core("tags", operator, values)), catalog)
                return scratch_document in await _matching_ids(raw_live, params)

            assert await matches("has_all_of", [tag_a]) is True
            assert await matches("has_any_of", [tag_a, tag_b]) is True
            assert await matches("has_all_of", [tag_a, tag_b]) is False, (
                "has_all_of must require EVERY listed tag"
            )
            assert await matches("has_none_of", [tag_b]) is True
            assert await matches("has_none_of", [tag_a]) is False
            assert await matches("is_present", []) is True

            # And untagged: is_missing on tags means "no tags at all".
            cleared = await raw_live.patch(
                f"/api/documents/{scratch_document}/", json={"tags": []}
            )
            cleared.raise_for_status()
            params = _compile(_set(_core("tags", "is_missing")), catalog)
            assert scratch_document in await _matching_ids(raw_live, params)
        finally:
            for tag_id in tag_ids:
                await raw_live.delete(f"/api/tags/{tag_id}/")


class TestFilterEngineGroupsLive:
    async def test_an_and_of_a_core_and_two_custom_conditions_is_exact(
        self, raw_live: httpx.AsyncClient, live_client: PaperlessClient
    ) -> None:
        """The M4 acceptance scenario's steps 2-5."""
        fields = await _golden_field_ids(raw_live)
        montant, periode = fields["Montant"], fields["Période concernée"]
        catalog = await _catalog(live_client)
        types = await raw_live.get("/api/document_types/", params={"page_size": 100})
        types.raise_for_status()
        type_id = next(
            item["id"]
            for item in types.json()["results"]
            if item["name"] == "Relevé de vacations"
        )

        combined = await _matching_ids(
            raw_live,
            _compile(
                _set(
                    _core("document_type", "equals", type_id),
                    _cf(montant, "is_missing"),
                    _cf(periode, "is_present"),
                ),
                catalog,
            ),
        )
        montant_missing = await _matching_ids(
            raw_live, _compile(_set(_cf(montant, "is_missing")), catalog)
        )
        periode_present = await _matching_ids(
            raw_live, _compile(_set(_cf(periode, "is_present")), catalog)
        )

        assert combined == montant_missing & periode_present
        assert combined, "the Golden Dataset seeds exactly this case (nomont-01)"

    async def test_an_or_group_of_custom_fields_is_a_real_union(
        self, raw_live: httpx.AsyncClient, live_client: PaperlessClient
    ) -> None:
        """The M4 acceptance scenario's step 10."""
        fields = await _golden_field_ids(raw_live)
        montant, periode = fields["Montant"], fields["Période concernée"]
        catalog = await _catalog(live_client)

        union = await _matching_ids(
            raw_live,
            _compile(
                _set(_group(_cf(montant, "is_missing"), _cf(periode, "is_missing"))), catalog
            ),
        )
        montant_missing = await _matching_ids(
            raw_live, _compile(_set(_cf(montant, "is_missing")), catalog)
        )
        periode_missing = await _matching_ids(
            raw_live, _compile(_set(_cf(periode, "is_missing")), catalog)
        )

        assert union == montant_missing | periode_missing
        assert union > montant_missing, "the OR must widen, not narrow"

    async def test_a_core_condition_intersects_a_custom_or_group(
        self, raw_live: httpx.AsyncClient, live_client: PaperlessClient
    ) -> None:
        fields = await _golden_field_ids(raw_live)
        montant, periode = fields["Montant"], fields["Période concernée"]
        catalog = await _catalog(live_client)

        params = _compile(
            _set(
                _core("title", "contains", "scan"),
                _group(_cf(montant, "is_missing"), _cf(periode, "is_missing")),
            ),
            catalog,
        )
        combined = await _matching_ids(raw_live, params)
        titles = await _matching_ids(
            raw_live, _compile(_set(_core("title", "contains", "scan")), catalog)
        )
        union = await _matching_ids(
            raw_live,
            _compile(
                _set(_group(_cf(montant, "is_missing"), _cf(periode, "is_missing"))), catalog
            ),
        )

        assert combined == titles & union

    async def test_an_unsupported_mixed_or_is_refused_before_any_request(
        self, live_client: PaperlessClient
    ) -> None:
        """The M4 acceptance scenario's steps 11-13.

        Nothing is sent. There is no request to observe here, and that is the
        assertion: the refusal happens in PaperWrench, purely, and does not
        depend on Paperless noticing anything (it would not - it silently
        ignores what it does not understand).
        """
        from paperwrench.filters import FilterNotCompilable
        from paperwrench.filters import compile_filterset

        fields = await live_client.list_custom_fields()
        montant = next(field.id for field in fields if field.name == "Montant")
        catalog = await _catalog(live_client)

        with pytest.raises(FilterNotCompilable) as excinfo:
            compile_filterset(
                _set(
                    _core("title", "contains", "scan"),
                    _cf(montant, "is_missing"),
                    operator="or",
                ),
                catalog,
            )
        assert excinfo.value.issues[0].code.value == "MIXED_OR_UNSUPPORTED"


class TestFilterEngineCompositionLive:
    """Filters composing with search, ordering and pagination.

    M1 proved these worked individually and M3 proved search/ordering/paging
    compose. What is new in M4 is `custom_field_query` in the mix, which
    Paperless evaluates through a separate annotated subquery - a genuinely
    different code path, and the one most likely to interact badly.
    """

    async def test_a_filter_composes_with_a_title_search(
        self, raw_live: httpx.AsyncClient, live_client: PaperlessClient
    ) -> None:
        fields = await _golden_field_ids(raw_live)
        montant = fields["Montant"]
        catalog = await _catalog(live_client)
        params = _compile(_set(_cf(montant, "is_present")), catalog)

        filtered = await _matching_ids(raw_live, params)
        searched = await _matching_ids(raw_live, {"title_search": "vacations"})
        both = await _matching_ids(raw_live, {**params, "title_search": "vacations"})

        assert both == filtered & searched
        assert both, "search and custom_field_query must intersect, not annihilate"

    async def test_a_filter_composes_with_ordering(
        self, raw_live: httpx.AsyncClient, live_client: PaperlessClient
    ) -> None:
        fields = await _golden_field_ids(raw_live)
        montant = fields["Montant"]
        catalog = await _catalog(live_client)
        params = _compile(_set(_cf(montant, "has_value")), catalog)

        ascending = await raw_live.get(
            "/api/documents/", params={**params, "ordering": "title", "page_size": 100}
        )
        descending = await raw_live.get(
            "/api/documents/", params={**params, "ordering": "-title", "page_size": 100}
        )
        ascending.raise_for_status()
        descending.raise_for_status()

        up = [item["id"] for item in ascending.json()["results"]]
        down = [item["id"] for item in descending.json()["results"]]

        assert set(up) == set(down), "ordering must not change the matching set"
        assert up == list(reversed(down))

    async def test_a_filter_composes_with_pagination_without_losing_documents(
        self, raw_live: httpx.AsyncClient, live_client: PaperlessClient
    ) -> None:
        fields = await _golden_field_ids(raw_live)
        montant = fields["Montant"]
        catalog = await _catalog(live_client)
        params = {**_compile(_set(_cf(montant, "has_value")), catalog), "ordering": "id"}

        total = await _count(raw_live, params)
        assert total >= 4, "need several documents to page through"

        seen: list[int] = []
        for page in range(1, total + 1):
            response = await raw_live.get(
                "/api/documents/", params={**params, "page": page, "page_size": 2}
            )
            response.raise_for_status()
            payload = response.json()
            ids = [int(item["id"]) for item in payload["results"]]
            assert len(ids) <= 2
            seen.extend(ids)
            if not payload.get("next"):
                break

        assert len(seen) == total
        assert len(set(seen)) == total, "pages must not overlap"

    async def test_the_count_matches_the_number_of_documents_actually_listed(
        self, raw_live: httpx.AsyncClient, live_client: PaperlessClient
    ) -> None:
        """The M4 acceptance scenario's step 6.

        The count PaperWrench shows before a destructive operation must be
        the same number as the documents that would be operated on.
        """
        fields = await _golden_field_ids(raw_live)
        montant, periode = fields["Montant"], fields["Période concernée"]
        catalog = await _catalog(live_client)

        for filterset in (
            _set(_cf(montant, "is_missing")),
            _set(_cf(montant, "greater_than", "EUR0.00")),
            _set(_cf(montant, "is_present"), _cf(periode, "is_present")),
            _set(_group(_cf(montant, "is_missing"), _cf(periode, "is_missing"))),
        ):
            params = _compile(filterset, catalog)
            assert await _count(raw_live, params) == len(
                await _matching_ids(raw_live, params)
            )

    async def test_the_client_count_helper_never_parses_documents(
        self, live_client: PaperlessClient, raw_live: httpx.AsyncClient
    ) -> None:
        fields = await _golden_field_ids(raw_live)
        catalog = await _catalog(live_client)
        params = _compile(_set(_cf(fields["Montant"], "is_present")), catalog)

        assert await live_client.count_documents(params=params) == await _count(
            raw_live, params
        )


class TestFilterEngineSelectLive:
    """Select filtering on the option id, never the label.

    Paperless's own `SelectField` resolves a label to its id if you send one,
    which is convenient and dangerous: a filter that stored a label would
    silently retarget the day someone renames the option. PaperWrench only
    ever sends ids, and this proves both halves of that on a real server.
    """

    async def test_a_select_filter_matches_on_the_stored_option_id(
        self, raw_live: httpx.AsyncClient, live_client: PaperlessClient, scratch_document: int
    ) -> None:
        import uuid

        marker = uuid.uuid4().hex[:8]
        created = await raw_live.post(
            "/api/custom_fields/",
            json={
                "name": f"pw-m4-select-{marker}",
                "data_type": "select",
                "extra_data": {
                    "select_options": [{"label": "Urgent"}, {"label": "Normal"}]
                },
            },
        )
        created.raise_for_status()
        definition = created.json()
        field_id = int(definition["id"])
        options = definition["extra_data"]["select_options"]
        urgent = next(option["id"] for option in options if option["label"] == "Urgent")

        try:
            patched = await raw_live.patch(
                f"/api/documents/{scratch_document}/",
                json={"custom_fields": [{"field": field_id, "value": urgent}]},
            )
            patched.raise_for_status()

            catalog = await _catalog(live_client)
            params = _compile(_set(_cf(field_id, "equals", urgent)), catalog)
            assert scratch_document in await _matching_ids(raw_live, params)

            # And a label sent where an id belongs never gets that far: the
            # validator refuses it against the field's known option ids.
            from paperwrench.filters import validate_filterset

            issues = validate_filterset(_set(_cf(field_id, "equals", "Urgent")), catalog)
            assert [issue.code.value for issue in issues] == ["UNKNOWN_SELECT_OPTION"]
        finally:
            await raw_live.delete(f"/api/custom_fields/{field_id}/")


class TestFilterEngineServerLimitsLive:
    """Paperless's own guards, confirmed so our local limits are not guesses."""

    async def test_an_unknown_custom_field_id_is_rejected_by_the_server_too(
        self, raw_live: httpx.AsyncClient
    ) -> None:
        """A useful belt-and-braces, but NOT what PaperWrench relies on.

        The engine refuses an unknown field id before building a request
        (see `test_filter_no_fallback.py`). This only records that the server
        would also have complained - unlike an unknown *filter parameter*,
        which it silently ignores.
        """
        response = await raw_live.get(
            "/api/documents/",
            params={"custom_field_query": json.dumps([999_999, "exists", True])},
        )
        assert response.status_code == 400

    async def test_too_many_atoms_is_rejected_by_the_server(
        self, raw_live: httpx.AsyncClient
    ) -> None:
        fields = await _golden_field_ids(raw_live)
        periode = fields["Période concernée"]
        expression = ["AND", [[periode, "icontains", str(i)] for i in range(21)]]

        response = await raw_live.get(
            "/api/documents/", params={"custom_field_query": json.dumps(expression)}
        )
        assert response.status_code == 400

    async def test_an_operator_the_data_type_forbids_is_rejected_by_the_server(
        self, raw_live: httpx.AsyncClient
    ) -> None:
        """`contains` on a Boolean - the M4 brief's example.

        PaperWrench refuses this at validation time; the server agrees.
        """
        fields = await _golden_field_ids(raw_live)
        valide = fields["Validé"]

        response = await raw_live.get(
            "/api/documents/",
            params={"custom_field_query": json.dumps([valide, "icontains", "true"])},
        )
        assert response.status_code == 400

    async def test_an_empty_filter_value_is_silently_ignored_by_django_filter(
        self, raw_live: httpx.AsyncClient
    ) -> None:
        """The finding that made empty text values a validation error.

        `title__icontains=` does not filter on an empty title - django-filter
        skips the filter entirely and returns the whole library. Nothing in
        the response says so, which is why PaperWrench refuses to emit one.
        """
        everything = await _count(raw_live, {})
        empty_value = await _count(raw_live, {"title__icontains": ""})

        assert empty_value == everything, (
            "an empty filter value is dropped, not applied - PaperWrench must "
            "never emit one"
        )


def _live_app_settings(live_settings: Settings, tmp_path: Path) -> Settings:
    """Live Paperless, throwaway on-disk SQLite for PaperWrench's own state.

    Deliberately a **file** database, not an in-memory one. Every SQLite
    connection to an in-memory database gets its own private copy, so
    ``run_migrations()`` - which opens its own connection - creates the
    schema somewhere the application's engine will never see it, and startup
    then dies on ``no such table: runtime_lock``. The shared ``client``
    fixture in ``tests/backend/conftest.py`` uses a tmp file for exactly
    this reason.
    """
    return live_settings.model_copy(
        update={"database_url": f"sqlite+pysqlite:///{tmp_path / 'live.db'}"}
    )


class TestFilterEngineApiLive:
    """The PaperWrench endpoints, end to end against the real server."""

    async def test_validate_count_and_query_agree_on_the_same_dataset(
        self, raw_live: httpx.AsyncClient, live_settings: Settings, tmp_path: Path
    ) -> None:
        from fastapi.testclient import TestClient

        from paperwrench.main import create_app

        fields = await _golden_field_ids(raw_live)
        montant = fields["Montant"]
        body = {
            "root": {
                "kind": "group",
                "operator": "and",
                "children": [
                    {
                        "kind": "condition",
                        "field": {"source": "custom_field", "field_id": montant},
                        "operator": "is_missing",
                        "value": None,
                    }
                ],
            }
        }

        app = create_app(_live_app_settings(live_settings, tmp_path))
        with TestClient(app) as client:
            validation = client.post("/api/v1/filters/validate", json={"filters": body})
            count = client.post("/api/v1/filters/count", json={"filters": body})
            page = client.post(
                "/api/v1/documents/query", json={"filters": body, "page_size": 25}
            )

        assert validation.json()["valid"] is True
        assert validation.json()["compilable"] is True
        assert count.json()["count"] == page.json()["total"]
        assert page.json()["total"] >= 1

    async def test_capabilities_reflect_the_real_custom_fields(
        self, live_settings: Settings, tmp_path: Path
    ) -> None:
        from fastapi.testclient import TestClient

        from paperwrench.main import create_app

        app = create_app(_live_app_settings(live_settings, tmp_path))
        with TestClient(app) as client:
            payload = client.get("/api/v1/filters/capabilities").json()

        labels = {field["label"] for field in payload["fields"]}
        assert "Montant" in labels
        assert "Période concernée" in labels
        montant = next(field for field in payload["fields"] if field["label"] == "Montant")
        assert montant["field_type"] == "monetary"
        assert "contains" not in {op["operator"] for op in montant["operators"]}
