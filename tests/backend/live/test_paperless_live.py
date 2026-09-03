"""Tests executed against a REAL Paperless-ngx 3.1.2 instance.

Everything here is an assertion about the *server*, not about PaperWrench.
A mocked test can only prove we handle a shape we invented; these prove the
shape is real. When one of these fails after a Paperless upgrade, the finding
belongs in docs/paperless-api.md before any code is changed.

Gated by tests/backend/live/conftest.py: requires
``PAPERWRENCH_ALLOW_LIVE_TESTS=true`` AND an authorised sandbox host.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from paperwrench.config import Settings
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
            ("Periode concernee", "Février 2024 — Hôpital Saint-Joseph (créé)"),
            ("Periode concernee", ""),
            ("Periode concernee", None),
            ("Montant", "EUR1234.56"),
            ("Montant", "EUR0.00"),
            ("Valide", True),
            ("Valide", False),
            ("Date de reglement", "2024-03-15"),
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
