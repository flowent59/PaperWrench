"""Mocked integration tests for PaperlessClient (respx, no network).

These are NOT live tests. Every response here is one we wrote, so they can
only prove that the client behaves correctly *given* a response shape - never
that the shape is real. The shapes are copied from real 3.1.2 captures, and
tests/backend/live/ re-checks them against an actual server.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from paperwrench.config import Settings
from paperwrench.errors import PaperlessIncompatibleError
from paperwrench.errors import PaperlessNotConfiguredError
from paperwrench.errors import PaperlessUnauthorizedError
from paperwrench.errors import PaperlessUnreachableError
from paperwrench.paperless import PaperlessClient
from paperwrench.paperless.errors import PaperlessApiError
from paperwrench.paperless.errors import PaperlessConflictError
from paperwrench.paperless.errors import PaperlessNotFoundError
from paperwrench.paperless.errors import PaperlessValidationError

BASE = "http://paperless.test"
TOKEN = "test-token-abcdef123456"

V10_HEADERS = {"X-Api-Version": "10", "X-Version": "3.1.2"}


@pytest.fixture
def paperless_settings() -> Settings:
    return Settings(
        PAPERLESS_URL=BASE,
        PAPERLESS_TOKEN=TOKEN,
        PAPERWRENCH_DATABASE_URL="sqlite+pysqlite:///:memory:",
    )


def _page(results: list[dict[str, Any]], *, count: int | None = None,
          next_url: str | None = None) -> dict[str, Any]:
    return {
        "count": count if count is not None else len(results),
        "next": next_url,
        "previous": None,
        "results": results,
    }


def _doc(doc_id: int, **kwargs: Any) -> dict[str, Any]:
    base = {"id": doc_id, "title": f"doc-{doc_id}", "custom_fields": [], "tags": []}
    base.update(kwargs)
    return base


# --------------------------------------------------------------------- headers
class TestRequestConstruction:
    @respx.mock
    async def test_sends_token_and_negotiates_api_version_10(
        self, paperless_settings: Settings
    ) -> None:
        route = respx.get(f"{BASE}/api/documents/1/").mock(
            return_value=httpx.Response(200, json=_doc(1), headers=V10_HEADERS)
        )
        async with PaperlessClient(paperless_settings) as client:
            await client.get_document(1)

        request = route.calls.last.request
        assert request.headers["Authorization"] == f"Token {TOKEN}"
        assert request.headers["Accept"] == "application/json; version=10"

    @respx.mock
    async def test_records_server_versions_from_response_headers(
        self, paperless_settings: Settings
    ) -> None:
        respx.get(f"{BASE}/api/documents/1/").mock(
            return_value=httpx.Response(200, json=_doc(1), headers=V10_HEADERS)
        )
        async with PaperlessClient(paperless_settings) as client:
            await client.get_document(1)
            assert client.api_version == "10"
            assert client.paperless_version == "3.1.2"

    async def test_refuses_to_build_a_client_without_configuration(self) -> None:
        unconfigured = Settings(
            PAPERLESS_URL="", PAPERLESS_TOKEN="",
            PAPERWRENCH_DATABASE_URL="sqlite+pysqlite:///:memory:",
        )
        with pytest.raises(PaperlessNotConfiguredError):
            async with PaperlessClient(unconfigured) as client:
                await client.get_document(1)

    @respx.mock
    async def test_a_redirect_is_an_error_not_a_success(
        self, paperless_settings: Settings
    ) -> None:
        """3.1.2 302s /api/; silently following redirects hides misconfiguration."""
        respx.get(f"{BASE}/api/documents/1/").mock(
            return_value=httpx.Response(302, headers={"Location": "/accounts/login/"})
        )
        async with PaperlessClient(paperless_settings) as client:
            with pytest.raises(PaperlessApiError, match="redirected"):
                await client.get_document(1)


# ------------------------------------------------------------------ error map
class TestErrorNormalisation:
    @respx.mock
    @pytest.mark.parametrize("status", [401, 403])
    async def test_auth_failures(self, paperless_settings: Settings, status: int) -> None:
        respx.get(f"{BASE}/api/documents/1/").mock(
            return_value=httpx.Response(status, json={"detail": "Invalid token."})
        )
        async with PaperlessClient(paperless_settings) as client:
            with pytest.raises(PaperlessUnauthorizedError):
                await client.get_document(1)

    @respx.mock
    async def test_406_maps_to_incompatible(self, paperless_settings: Settings) -> None:
        respx.get(f"{BASE}/api/documents/1/").mock(
            return_value=httpx.Response(
                406, json={"detail": 'Invalid version in "Accept" header.'}
            )
        )
        async with PaperlessClient(paperless_settings) as client:
            with pytest.raises(PaperlessIncompatibleError):
                await client.get_document(1)

    @respx.mock
    async def test_404_maps_to_not_found(self, paperless_settings: Settings) -> None:
        respx.get(f"{BASE}/api/documents/999999/").mock(
            return_value=httpx.Response(
                404, json={"detail": "No Document matches the given query."}
            )
        )
        async with PaperlessClient(paperless_settings) as client:
            with pytest.raises(PaperlessNotFoundError):
                await client.get_document(999999)

    @respx.mock
    async def test_400_maps_to_validation_error(self, paperless_settings: Settings) -> None:
        respx.get(f"{BASE}/api/documents/1/").mock(
            return_value=httpx.Response(200, json=_doc(1), headers=V10_HEADERS)
        )
        respx.patch(f"{BASE}/api/documents/1/").mock(
            return_value=httpx.Response(
                400,
                json={"custom_fields": [{"non_field_errors": ["Must be a two-decimal number"]}]},
            )
        )
        async with PaperlessClient(paperless_settings) as client:
            with pytest.raises(PaperlessValidationError):
                await client.update_custom_fields(1, [{"field": 2, "value": "EUR1,00"}])

    @respx.mock
    async def test_409_maps_to_conflict(self, paperless_settings: Settings) -> None:
        respx.get(f"{BASE}/api/documents/1/").mock(return_value=httpx.Response(409, text="nope"))
        async with PaperlessClient(paperless_settings) as client:
            with pytest.raises(PaperlessConflictError):
                await client.get_document(1)

    @respx.mock
    async def test_409_is_not_retryable_by_default(self, paperless_settings: Settings) -> None:
        """A 409 must never be treated as retryable by default (see errors.py).

        A conflict reflects the *state* Paperless found, not a transient
        transport problem. Anything that later builds automatic retries (the
        Job Engine, forward conflict detection) must be able to rely on this
        flag to refuse a blind retry loop.
        """
        respx.get(f"{BASE}/api/documents/1/").mock(return_value=httpx.Response(409, text="nope"))
        async with PaperlessClient(paperless_settings) as client:
            with pytest.raises(PaperlessConflictError) as excinfo:
                await client.get_document(1)
        assert excinfo.value.retryable is False

    @respx.mock
    async def test_500_is_retryable(self, paperless_settings: Settings) -> None:
        respx.get(f"{BASE}/api/documents/1/").mock(return_value=httpx.Response(500, text="boom"))
        async with PaperlessClient(paperless_settings) as client:
            with pytest.raises(PaperlessApiError) as excinfo:
                await client.get_document(1)
        assert excinfo.value.retryable is True

    @respx.mock
    async def test_connection_error_maps_to_unreachable(
        self, paperless_settings: Settings
    ) -> None:
        respx.get(f"{BASE}/api/documents/1/").mock(
            side_effect=httpx.ConnectError("connection refused")
        )
        async with PaperlessClient(paperless_settings) as client:
            with pytest.raises(PaperlessUnreachableError):
                await client.get_document(1)

    @respx.mock
    async def test_timeout_maps_to_unreachable(self, paperless_settings: Settings) -> None:
        respx.get(f"{BASE}/api/documents/1/").mock(
            side_effect=httpx.ReadTimeout("too slow")
        )
        async with PaperlessClient(paperless_settings) as client:
            with pytest.raises(PaperlessUnreachableError) as excinfo:
                await client.get_document(1)
        assert (excinfo.value.details or {}).get("reason") == "timeout"

    @respx.mock
    @pytest.mark.parametrize("status", [401, 404, 500])
    async def test_no_error_ever_leaks_the_token(
        self, paperless_settings: Settings, status: int
    ) -> None:
        """The token must not survive into a message, details blob or repr."""
        respx.get(f"{BASE}/api/documents/1/").mock(
            return_value=httpx.Response(status, text=f"failed with token {TOKEN}")
        )
        async with PaperlessClient(paperless_settings) as client:
            with pytest.raises(Exception) as excinfo:
                await client.get_document(1)

        exc = excinfo.value
        rendered = f"{exc!r} {exc} {getattr(exc, 'details', None)!r}"
        # The upstream body is echoed back, so a server that reflects the
        # token would leak it. That is exactly what we assert against.
        assert TOKEN not in rendered


# ------------------------------------------------------------------ pagination
class TestPagination:
    @respx.mock
    async def test_follows_next_across_pages(self, paperless_settings: Settings) -> None:
        respx.get(f"{BASE}/api/documents/", params={"page": "1"}).mock(
            return_value=httpx.Response(
                200,
                json=_page([_doc(1), _doc(2)], count=3, next_url=f"{BASE}/api/documents/?page=2"),
                headers=V10_HEADERS,
            )
        )
        respx.get(f"{BASE}/api/documents/", params={"page": "2"}).mock(
            return_value=httpx.Response(200, json=_page([_doc(3)], count=3), headers=V10_HEADERS)
        )
        async with PaperlessClient(paperless_settings) as client:
            documents = await client.iter_documents(page_size=2)

        assert [d.id for d in documents] == [1, 2, 3]

    @respx.mock
    async def test_stops_when_next_is_null(self, paperless_settings: Settings) -> None:
        route = respx.get(f"{BASE}/api/documents/").mock(
            return_value=httpx.Response(200, json=_page([_doc(1)]), headers=V10_HEADERS)
        )
        async with PaperlessClient(paperless_settings) as client:
            documents = await client.iter_documents()
        assert len(documents) == 1
        assert route.call_count == 1

    @respx.mock
    async def test_never_reads_the_v9_all_key(self, paperless_settings: Settings) -> None:
        """Pagination must work even though `all` is absent under v10."""
        payload = _page([_doc(1)])
        assert "all" not in payload
        respx.get(f"{BASE}/api/documents/").mock(
            return_value=httpx.Response(200, json=payload, headers=V10_HEADERS)
        )
        async with PaperlessClient(paperless_settings) as client:
            assert len(await client.iter_documents()) == 1

    @respx.mock
    async def test_page_size_is_capped(self, paperless_settings: Settings) -> None:
        route = respx.get(f"{BASE}/api/documents/").mock(
            return_value=httpx.Response(200, json=_page([]), headers=V10_HEADERS)
        )
        async with PaperlessClient(paperless_settings) as client:
            await client.iter_documents(page_size=99999)
        assert route.calls.last.request.url.params["page_size"] == "250"

    @respx.mock
    async def test_extra_filter_params_are_forwarded(
        self, paperless_settings: Settings
    ) -> None:
        route = respx.get(f"{BASE}/api/documents/").mock(
            return_value=httpx.Response(200, json=_page([]), headers=V10_HEADERS)
        )
        async with PaperlessClient(paperless_settings) as client:
            await client.iter_documents(params={"document_type__id": 3})
        assert route.calls.last.request.url.params["document_type__id"] == "3"


# ------------------------------------------------------------- custom fields
class TestCustomFieldSafety:
    @respx.mock
    async def test_patch_sends_the_complete_merged_collection(
        self, paperless_settings: Settings
    ) -> None:
        """The central regression test of the entire project.

        The document has five fields; we change one. The outgoing PATCH must
        still contain five, or Paperless deletes the other four.
        """
        existing = [
            {"field": 1, "value": "Janvier 2024"},
            {"field": 2, "value": "EUR450.00"},
            {"field": 3, "value": "REF-001"},
            {"field": 4, "value": "Hopital Saint-Joseph"},
            {"field": 7, "value": "Commentaire d'origine"},
        ]
        respx.get(f"{BASE}/api/documents/1/").mock(
            return_value=httpx.Response(
                200, json=_doc(1, custom_fields=existing), headers=V10_HEADERS
            )
        )
        patch = respx.patch(f"{BASE}/api/documents/1/").mock(
            return_value=httpx.Response(
                200, json=_doc(1, custom_fields=existing), headers=V10_HEADERS
            )
        )

        async with PaperlessClient(paperless_settings) as client:
            await client.update_custom_fields(1, [{"field": 2, "value": "EUR999.99"}])

        sent = patch.calls.last.request.read()
        import json as _json

        payload = _json.loads(sent)["custom_fields"]
        assert len(payload) == 5, "a partial payload would DELETE the omitted fields"
        assert {item["field"] for item in payload} == {1, 2, 3, 4, 7}
        assert next(i for i in payload if i["field"] == 2)["value"] == "EUR999.99"
        assert next(i for i in payload if i["field"] == 7)["value"] == "Commentaire d'origine"

    @respx.mock
    async def test_reads_before_writing(self, paperless_settings: Settings) -> None:
        get = respx.get(f"{BASE}/api/documents/1/").mock(
            return_value=httpx.Response(200, json=_doc(1), headers=V10_HEADERS)
        )
        respx.patch(f"{BASE}/api/documents/1/").mock(
            return_value=httpx.Response(200, json=_doc(1), headers=V10_HEADERS)
        )
        async with PaperlessClient(paperless_settings) as client:
            await client.update_custom_fields(1, [{"field": 1, "value": "x"}])
        assert get.call_count == 1, "the merge base must be read immediately before the write"

    @respx.mock
    async def test_forward_conflict_refuses_a_stale_base(
        self, paperless_settings: Settings
    ) -> None:
        from paperwrench.paperless.models import CustomFieldValue

        respx.get(f"{BASE}/api/documents/1/").mock(
            return_value=httpx.Response(
                200,
                json=_doc(1, custom_fields=[{"field": 1, "value": "CHANGED BY SOMEONE ELSE"}]),
                headers=V10_HEADERS,
            )
        )
        patch = respx.patch(f"{BASE}/api/documents/1/").mock(
            return_value=httpx.Response(200, json=_doc(1), headers=V10_HEADERS)
        )

        async with PaperlessClient(paperless_settings) as client:
            with pytest.raises(PaperlessConflictError):
                await client.update_custom_fields(
                    1,
                    [{"field": 1, "value": "new"}],
                    expected_before=[CustomFieldValue(field=1, value="what we saw earlier")],
                )

        assert patch.call_count == 0, "a conflicting document must not be written at all"

    @respx.mock
    async def test_matching_expected_state_is_written(
        self, paperless_settings: Settings
    ) -> None:
        from paperwrench.paperless.models import CustomFieldValue

        respx.get(f"{BASE}/api/documents/1/").mock(
            return_value=httpx.Response(
                200,
                json=_doc(1, custom_fields=[{"field": 1, "value": "unchanged"}]),
                headers=V10_HEADERS,
            )
        )
        patch = respx.patch(f"{BASE}/api/documents/1/").mock(
            return_value=httpx.Response(200, json=_doc(1), headers=V10_HEADERS)
        )
        async with PaperlessClient(paperless_settings) as client:
            await client.update_custom_fields(
                1,
                [{"field": 1, "value": "new"}],
                expected_before=[CustomFieldValue(field=1, value="unchanged")],
            )
        assert patch.call_count == 1

    @respx.mock
    async def test_update_document_does_not_touch_custom_fields(
        self, paperless_settings: Settings
    ) -> None:
        """Scalar PATCH is genuinely partial: it must not carry custom_fields."""
        patch = respx.patch(f"{BASE}/api/documents/1/").mock(
            return_value=httpx.Response(200, json=_doc(1), headers=V10_HEADERS)
        )
        async with PaperlessClient(paperless_settings) as client:
            await client.update_document(1, {"title": "Nouveau titre"})

        import json as _json

        payload = _json.loads(patch.calls.last.request.read())
        assert payload == {"title": "Nouveau titre"}
        assert "custom_fields" not in payload


# ------------------------------------------------------------------- probing
class TestCheckConnection:
    @respx.mock
    async def test_reports_a_healthy_compatible_instance(
        self, paperless_settings: Settings
    ) -> None:
        respx.get(f"{BASE}/api/documents/").mock(
            return_value=httpx.Response(200, json=_page([], count=24), headers=V10_HEADERS)
        )
        async with PaperlessClient(paperless_settings) as client:
            status = await client.check_connection()

        assert status.configured and status.connected and status.compatible
        assert status.api_version == "10"
        assert status.paperless_version == "3.1.2"
        assert status.document_count == 24
        assert status.error_code is None

    async def test_reports_unconfigured_without_any_request(self) -> None:
        unconfigured = Settings(
            PAPERLESS_URL="", PAPERLESS_TOKEN="",
            PAPERWRENCH_DATABASE_URL="sqlite+pysqlite:///:memory:",
        )
        async with PaperlessClient(unconfigured) as client:
            status = await client.check_connection()
        assert status.configured is False
        assert status.error_code == "PAPERLESS_NOT_CONFIGURED"

    @respx.mock
    async def test_reports_unreachable_instead_of_raising(
        self, paperless_settings: Settings
    ) -> None:
        respx.get(f"{BASE}/api/documents/").mock(side_effect=httpx.ConnectError("refused"))
        async with PaperlessClient(paperless_settings) as client:
            status = await client.check_connection()
        assert status.connected is False
        assert status.error_code == "PAPERLESS_UNREACHABLE"

    @respx.mock
    async def test_reports_bad_token_as_connected_but_incompatible(
        self, paperless_settings: Settings
    ) -> None:
        respx.get(f"{BASE}/api/documents/").mock(
            return_value=httpx.Response(401, json={"detail": "Invalid token."})
        )
        async with PaperlessClient(paperless_settings) as client:
            status = await client.check_connection()
        assert status.connected is True, "the server answered; the credential is what failed"
        assert status.compatible is False
        assert status.error_code == "PAPERLESS_UNAUTHORIZED"

    @respx.mock
    async def test_reports_406_as_incompatible(self, paperless_settings: Settings) -> None:
        respx.get(f"{BASE}/api/documents/").mock(
            return_value=httpx.Response(
                406, json={"detail": 'Invalid version in "Accept" header.'}
            )
        )
        async with PaperlessClient(paperless_settings) as client:
            status = await client.check_connection()
        assert status.error_code == "PAPERLESS_INCOMPATIBLE"

    @respx.mock
    async def test_detects_a_server_serving_a_different_version(
        self, paperless_settings: Settings
    ) -> None:
        respx.get(f"{BASE}/api/documents/").mock(
            return_value=httpx.Response(
                200,
                json=_page([]),
                headers={"X-Api-Version": "9", "X-Version": "2.14.7"},
            )
        )
        async with PaperlessClient(paperless_settings) as client:
            status = await client.check_connection()
        assert status.connected is True
        assert status.compatible is False
        assert status.api_version == "9"

    @respx.mock
    async def test_status_never_contains_the_token(
        self, paperless_settings: Settings
    ) -> None:
        respx.get(f"{BASE}/api/documents/").mock(
            return_value=httpx.Response(200, json=_page([]), headers=V10_HEADERS)
        )
        async with PaperlessClient(paperless_settings) as client:
            status = await client.check_connection()
        assert TOKEN not in status.model_dump_json()
