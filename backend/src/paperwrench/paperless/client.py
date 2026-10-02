"""``PaperlessClient`` - the only place in PaperWrench that speaks HTTP to Paperless.

Scope note: this client is deliberately small. It carries the primitives M1
needs (connect, probe, read documents and custom fields, paginate, write
safely) plus the couple of reads M2 will immediately require. It is not an
exhaustive binding of the Paperless API, and it should not become one -
endpoints get added when a milestone actually needs them.

Behaviours encoded here that were VERIFIED_LIVE against Paperless-ngx 3.1.2:

* ``Accept: application/json; version=10`` negotiates v10; an unknown version
  yields ``406`` with ``{"detail": "Invalid version in \\"Accept\\" header."}``.
* Responses carry ``X-Api-Version`` and ``X-Version`` (``3.1.2``). Beware:
  ``X-Api-Version`` is the *highest supported* version, not the negotiated
  one - see :meth:`PaperlessClient.check_connection`.
* List envelopes are ``{count, next, previous, results}``; the v9-only ``all``
  key is absent under v10.
* A partial ``custom_fields`` PATCH deletes the omitted fields.
* ``GET /api/`` is a ``302`` to the schema view, so it is useless as a probe.
"""

from __future__ import annotations

import hashlib
import json as json_module
import types
from collections.abc import AsyncGenerator
from collections.abc import Callable
from copy import deepcopy
from typing import Any
from typing import Self
from typing import TypeGuard
from urllib.parse import urlsplit

import httpx
import structlog
from pydantic import SecretStr

from paperwrench.config import Settings
from paperwrench.errors import PaperlessForbiddenError
from paperwrench.errors import PaperlessIncompatibleError
from paperwrench.errors import PaperlessNotConfiguredError
from paperwrench.errors import PaperlessUnauthorizedError
from paperwrench.errors import PaperlessUnreachableError
from paperwrench.errors import PaperWrenchError
from paperwrench.logging import register_secret
from paperwrench.paperless.errors import PaperlessApiError
from paperwrench.paperless.errors import PaperlessConflictError
from paperwrench.paperless.errors import PaperlessNotFoundError
from paperwrench.paperless.errors import PaperlessValidationError
from paperwrench.paperless.models import ConnectionStatus
from paperwrench.paperless.models import Correspondent
from paperwrench.paperless.models import CustomField
from paperwrench.paperless.models import CustomFieldValue
from paperwrench.paperless.models import Document
from paperwrench.paperless.models import DocumentType
from paperwrench.paperless.models import Page
from paperwrench.paperless.models import StoragePath
from paperwrench.paperless.models import Tag
from paperwrench.paperless.models import merge_custom_fields
from paperwrench.paperless.mutations import DocumentMutationCoordinator
from paperwrench.paperless.mutations import MutationPlan
from paperwrench.paperless.mutations import MutationResult
from paperwrench.paperless.mutations import core_payload
from paperwrench.paperless.mutations import revision

logger = structlog.get_logger(__name__)


def _valid_identity(value: object) -> TypeGuard[dict[str, Any]]:
    return (
        isinstance(value, dict)
        and type(value.get("id")) is int
        and 0 < value["id"] < (1 << 63)
        and isinstance(value.get("username"), str)
        and bool(value["username"].strip())
        and len(value["username"]) <= 255
    )

# `GET /api/` 302-redirects to /api/schema/view/ on 3.1.2 (VERIFIED_LIVE), so
# the probe uses a cheap authenticated list instead.
PROBE_PATH = "/api/documents/"

# StandardPagination in 3.1.2 accepts page_size up to 100000. We stay far
# below that: the point of paginating is to avoid huge responses.
MAX_PAGE_SIZE = 250


def _redact(url: str) -> str:
    """Strip credentials and query strings before a URL reaches a log line."""
    parts = urlsplit(url)
    netloc = parts.hostname or ""
    if parts.port:
        netloc = f"{netloc}:{parts.port}"
    return f"{parts.scheme}://{netloc}{parts.path}"


class PaperlessClient:
    """Async client for a single Paperless-ngx instance.

    Use as an async context manager, or pass a pre-built ``httpx.AsyncClient``
    (which is how the tests inject ``respx``).
    """

    def __init__(
        self,
        settings: Settings,
        *,
        http_client: httpx.AsyncClient | None = None,
        coordinator: DocumentMutationCoordinator | None = None,
    ) -> None:
        self.coordinator = coordinator or DocumentMutationCoordinator(settings.max_concurrency)
        self._settings = settings
        self._external_client = http_client is not None
        self._client = http_client
        self._api_version: str | None = None
        self._paperless_version: str | None = None

    # ------------------------------------------------------------------ setup
    @property
    def base_url(self) -> str:
        return self._settings.paperless_url

    @property
    def api_version(self) -> str | None:
        """``X-Api-Version`` from the last response.

        This is the highest API version the server supports, NOT the version
        that was negotiated for that request (VERIFIED_LIVE on 3.1.2; the
        middleware sets it to ``ALLOWED_VERSIONS[-1]`` unconditionally).
        """
        return self._api_version

    @property
    def paperless_version(self) -> str | None:
        """``X-Version`` (the Paperless release) seen on the last response."""
        return self._paperless_version

    def _build_client(self) -> httpx.AsyncClient:
        if not self._settings.paperless_configured:
            raise PaperlessNotConfiguredError(
                "Paperless is not configured: set PAPERLESS_URL and PAPERLESS_TOKEN."
            )
        token = self._settings.paperless_token.get_secret_value()
        # Self-defence: the app registers the token at startup, but a client
        # built outside the app lifespan (a test, a future worker process)
        # would otherwise have no scrubbing in place for it.
        register_secret(token)
        return httpx.AsyncClient(
            base_url=self._settings.paperless_url,
            headers={
                # The token lives only in this header, on this client.
                "Authorization": f"Token {token}",
                "Accept": self._settings.accept_header,
                "User-Agent": "PaperWrench",
            },
            timeout=httpx.Timeout(
                connect=self._settings.paperless_timeout_connect,
                read=self._settings.paperless_timeout_read,
                write=self._settings.paperless_timeout_read,
                pool=self._settings.paperless_timeout_connect,
            ),
            verify=self._settings.paperless_verify_ssl,
            # Paperless 302s /api/ to the schema view; following redirects
            # blindly would turn a misconfiguration into a confusing success.
            follow_redirects=False,
        )

    async def __aenter__(self) -> Self:
        # Deliberately does NOT build the transport. `check_connection()` must
        # be able to report "not configured" as a state the UI can render;
        # raising from `__aenter__` would turn a normal first-run condition
        # into a 503 before the endpoint body ever executes. The transport is
        # created lazily on the first actual request.
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: types.TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._client is not None and not self._external_client:
            await self._client.aclose()
            self._client = None

    async def invalidate_credentials(self) -> None:
        """Close the transport and make a revoked session credential unusable."""
        await self.aclose()
        object.__setattr__(self._settings, "paperless_token", SecretStr(""))

    # ----------------------------------------------------------------- plumbing
    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
    ) -> httpx.Response:
        """Issue one request and normalise every failure mode.

        No caller outside this module ever sees an ``httpx`` exception.
        """
        if self._client is None:
            self._client = self._build_client()

        try:
            response = await self._client.request(method, path, params=params, json=json)
        except httpx.TimeoutException as exc:
            raise PaperlessUnreachableError(
                f"Paperless did not respond in time ({method} {path}).",
                details={"reason": "timeout"},
            ) from exc
        except httpx.TransportError as exc:
            # DNS failure, refused connection, TLS failure...
            raise PaperlessUnreachableError(
                f"Cannot reach Paperless at {_redact(self.base_url)}.",
                details={"reason": type(exc).__name__},
            ) from exc

        self._remember_versions(response)
        self._raise_for_status(response, method, path)
        return response

    def _remember_versions(self, response: httpx.Response) -> None:
        api_version = response.headers.get("x-api-version")
        paperless_version = response.headers.get("x-version")
        if api_version:
            self._api_version = api_version
        if paperless_version:
            self._paperless_version = paperless_version

    def _raise_for_status(self, response: httpx.Response, method: str, path: str) -> None:
        status = response.status_code
        if status < 400:
            # A redirect is not success: it means the URL is not what we think
            # it is (a reverse proxy, or a missing /api prefix).
            if 300 <= status < 400:
                raise PaperlessApiError(
                    f"Paperless redirected {method} {path}; check PAPERLESS_URL.",
                    upstream_status=status,
                    details={"location": _redact(response.headers.get("location", ""))},
                )
            return

        body = response.text

        if status == 401:
            # VERIFIED_LIVE (3.1.2): an invalid/unknown token -> 401 with
            # {"detail": "Invalid token."}. The credential itself is bad.
            raise PaperlessUnauthorizedError(
                "Paperless rejected the API token.",
                details={"upstream_status": status},
            )
        if status == 403:
            # VERIFIED_LIVE (3.1.2), reproduced with a deliberately
            # unprivileged sandbox user: a *valid* token with no permission
            # -> 403 with {"detail": "You do not have permission to perform
            # this action."}. The credential is fine; this actor may not do
            # this. Kept distinct from 401 - see PaperlessForbiddenError.
            raise PaperlessForbiddenError(
                f"Paperless authenticated the request but refused it ({method} {path}).",
                details={"upstream_status": status},
            )
        if status == 406:
            # VERIFIED_LIVE: this is exactly how 3.1.2 signals an API version
            # it cannot serve.
            raise PaperlessIncompatibleError(
                f"Paperless cannot serve API version {self._settings.paperless_api_version}.",
                details={"upstream_status": status, "upstream_body": body[:200]},
            )
        if status == 404:
            raise PaperlessNotFoundError(
                f"Paperless has no object at {path}.",
                upstream_status=status,
                upstream_body=body,
            )
        if status in (400, 422):
            raise PaperlessValidationError(
                f"Paperless rejected the payload for {method} {path}.",
                upstream_status=status,
                upstream_body=body,
            )
        if status == 409:
            raise PaperlessConflictError(
                f"Paperless reported a conflict for {method} {path}.",
                upstream_status=status,
                upstream_body=body,
            )
        raise PaperlessApiError(
            f"Paperless returned HTTP {status} for {method} {path}.",
            upstream_status=status,
            upstream_body=body,
            retryable=status >= 500,
        )

    # ---------------------------------------------------------------- probing
    async def check_connection(self) -> ConnectionStatus:
        """Probe reachability and API compatibility. Never raises.

        Returns a structure safe to hand to the browser: no token, no headers.
        """
        settings = self._settings
        if not settings.paperless_configured:
            return ConnectionStatus(
                configured=False,
                connected=False,
                compatible=False,
                url=settings.paperless_url or None,
                requested_api_version=settings.paperless_api_version,
                error_code="PAPERLESS_NOT_CONFIGURED",
                error_message="Set PAPERLESS_URL and PAPERLESS_TOKEN.",
            )

        try:
            response = await self._request("GET", PROBE_PATH, params={"page_size": 1})
        except (
            PaperlessUnreachableError,
            PaperlessUnauthorizedError,
            PaperlessForbiddenError,
            PaperlessIncompatibleError,
            PaperlessApiError,
        ) as exc:
            return ConnectionStatus(
                configured=True,
                connected=not isinstance(exc, PaperlessUnreachableError),
                compatible=False,
                url=settings.paperless_url,
                requested_api_version=settings.paperless_api_version,
                api_version=self._api_version,
                paperless_version=self._paperless_version,
                error_code=str(exc.code),
                error_message=exc.message,
            )

        payload = response.json()
        highest_supported = self._api_version

        # `X-Api-Version` is NOT the negotiated version.
        #
        # VERIFIED_LIVE (3.1.2), and confirmed in
        # src/paperless/middleware.py::ApiVersionMiddleware, which sets:
        #
        #     response["X-Api-Version"] = ALLOWED_VERSIONS[-1]
        #
        # It is therefore the HIGHEST version the server supports, constant
        # for a given release. Requesting v9 still returns `X-Api-Version: 10`
        # while the body is genuinely v9-shaped (it carries the `all` key).
        #
        # So comparing this header against what we asked for would flag every
        # correctly-negotiated request as incompatible. The real compatibility
        # signal is the status code: a version the server cannot serve gets a
        # 406, which is handled in the except branch above. Reaching this
        # point means our version was accepted.
        compatible = True
        note: str | None = None
        if highest_supported is not None:
            try:
                if int(highest_supported) < settings.paperless_api_version:
                    compatible = False
                    note = (
                        f"Paperless supports API versions up to {highest_supported}, "
                        f"but {settings.paperless_api_version} was requested."
                    )
            except ValueError:  # pragma: no cover - defensive
                pass

        return ConnectionStatus(
            configured=True,
            connected=True,
            compatible=compatible,
            url=settings.paperless_url,
            requested_api_version=settings.paperless_api_version,
            api_version=highest_supported,
            paperless_version=self._paperless_version,
            document_count=payload.get("count"),
            error_code=None if compatible else "PAPERLESS_INCOMPATIBLE",
            error_message=note,
        )

    async def get_profile(self) -> dict[str, Any]:
        """Return a normalized identity for the authenticated Paperless user.

        Paperless documents ``/api/profile/`` as the self-service endpoint,
        available without permission to enumerate other users. If identity is
        absent, read the authenticated user's identity from UI settings. Keep
        the historical owner key separate so existing data remains reachable.
        Raw upstream payloads (including auth_token) never leave this boundary.
        """
        response = await self._request("GET", "/api/profile/")
        payload = response.json()
        if not isinstance(payload, dict):
            raise PaperlessIncompatibleError("Paperless returned an invalid profile response.")
        token = self._settings.paperless_token.get_secret_value()
        try:
            user_id = int(payload["id"])
        except (KeyError, TypeError, ValueError):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            user_id = int.from_bytes(digest[:8], "big") & ((1 << 63) - 1)
            user_id = user_id or 1
        first_name = str(payload.get("first_name") or "").strip()
        last_name = str(payload.get("last_name") or "").strip()
        email = str(payload.get("email") or "").strip()
        display_name = " ".join(part for part in (first_name, last_name) if part)
        username = str(payload.get("username") or email or display_name or "Paperless user")
        identity = payload
        if not _valid_identity(identity):
            try:
                ui_response = await self._request("GET", "/api/ui_settings/")
                ui_payload = ui_response.json()
                candidate = ui_payload.get("user") if isinstance(ui_payload, dict) else None
                if _valid_identity(candidate) and (
                    "id" not in payload or payload["id"] == candidate["id"]
                ):
                    identity = candidate
            except PaperlessUnauthorizedError:
                raise
            except (PaperWrenchError, ValueError):
                # Missing UI permission/endpoint must not break token-only login.
                # Enrollment requires a verified identity and fails explicitly.
                pass
        verified = _valid_identity(identity)
        if verified:
            username = identity["username"]
        return {
            "id": user_id,
            "username": username,
            "identity_verified": verified,
            "upstream_user_id": identity["id"] if verified else None,
            "first_name": first_name,
            "last_name": last_name,
        }

    # -------------------------------------------------------------- pagination
    async def _get_page(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        response = await self._request("GET", path, params=params)
        payload: dict[str, Any] = response.json()
        return payload

    async def iter_pages(
        self, path: str, *, params: dict[str, Any] | None = None, page_size: int | None = None
    ) -> list[dict[str, Any]]:
        """Follow ``next`` to the end and return every result object.

        Deliberately does **not** use the v9 ``all`` key: it does not exist in
        v10 (VERIFIED_LIVE). Pages are followed by page number rather than by
        replaying the absolute ``next`` URL, because that URL is built from
        Paperless's own notion of its hostname, which behind a reverse proxy
        is frequently not a URL we can reach.
        """
        size = min(page_size or self._settings.default_page_size, MAX_PAGE_SIZE)
        query: dict[str, Any] = {**(params or {}), "page_size": size}

        results: list[dict[str, Any]] = []
        page = 1
        while True:
            payload = await self._get_page(path, {**query, "page": page})
            results.extend(payload.get("results", []))
            if not payload.get("next"):
                return results
            page += 1

    # --------------------------------------------------------------- documents
    async def get_document(self, document_id: int) -> Document:
        response = await self._request("GET", f"/api/documents/{document_id}/")
        return Document.model_validate(response.json())

    async def list_documents(
        self, *, params: dict[str, Any] | None = None, page: int = 1, page_size: int | None = None
    ) -> Page[Document]:
        """One page of documents. Use :meth:`iter_documents` to walk them all."""
        size = min(page_size or self._settings.default_page_size, MAX_PAGE_SIZE)
        payload = await self._get_page(
            "/api/documents/", {**(params or {}), "page": page, "page_size": size}
        )
        return Page[Document].model_validate(payload)

    async def count_documents(self, *, params: dict[str, Any] | None = None) -> int:
        """How many documents match ``params``, without fetching them.

        Counting is not fetching. This asks for the smallest page Paperless
        will serve and reads only the envelope's ``count``, so the cost is one
        request and one document's worth of payload regardless of whether the
        filter matches twelve documents or fifty thousand.

        The ``results`` array is deliberately never parsed here: the caller
        wants a number, and turning this into "fetch a page and count it"
        is the first step down the road ADR-0007 exists to block.

        (``page_size=0`` is not used: DRF's paginator does not define it, and
        a value the server may interpret differently across versions is not
        worth one document of payload.)
        """
        payload = await self._get_page(
            "/api/documents/", {**(params or {}), "page": 1, "page_size": 1}
        )
        return int(payload.get("count", 0))

    async def iter_documents(
        self, *, params: dict[str, Any] | None = None, page_size: int | None = None
    ) -> AsyncGenerator[Document, None]:
        """Yield documents lazily, retaining at most one server page.

        Consumers wanting a list must collect explicitly. Never follow an
        upstream absolute next URL (it may name a different origin).
        """
        page = 1
        while True:
            batch = await self.list_documents(params=params, page=page, page_size=page_size)
            for document in batch.results:
                yield document
            if batch.next is None:
                return
            page += 1

    async def get_document_metadata(self, document_id: int) -> dict[str, Any]:
        """``/metadata/`` sub-resource.

        VERIFIED_LIVE (3.1.2) keys: ``archive_checksum``,
        ``archive_media_filename``, ``archive_metadata``, ``archive_size``,
        ``has_archive_version``, ``lang``. Shape varies with the file, so this
        stays an untyped mapping on purpose.
        """
        response = await self._request("GET", f"/api/documents/{document_id}/metadata/")
        payload: dict[str, Any] = response.json()
        return payload

    # ----------------------------------------------------------- custom fields
    async def list_custom_fields(self) -> list[CustomField]:
        raw = await self.iter_pages("/api/custom_fields/")
        return [CustomField.model_validate(item) for item in raw]

    # -------------------------------------------------------------- metadata
    # Reference data (M2): tags, correspondents, document types and storage
    # paths. Every one of these is a small, fully-paginated list - none of
    # them are expected to be large enough to need anything beyond
    # `iter_pages`, and this is deliberately the same shape as
    # `list_custom_fields` above rather than a bespoke path per endpoint.
    async def list_tags(self) -> list[Tag]:
        raw = await self.iter_pages("/api/tags/")
        return [Tag.model_validate(item) for item in raw]

    async def list_correspondents(self) -> list[Correspondent]:
        raw = await self.iter_pages("/api/correspondents/")
        return [Correspondent.model_validate(item) for item in raw]

    async def list_document_types(self) -> list[DocumentType]:
        raw = await self.iter_pages("/api/document_types/")
        return [DocumentType.model_validate(item) for item in raw]

    async def list_storage_paths(self) -> list[StoragePath]:
        raw = await self.iter_pages("/api/storage_paths/")
        return [StoragePath.model_validate(item) for item in raw]

    async def update_document(self, document_id: int, payload: dict[str, Any]) -> Document:
        """Core-only low-level write. Reject every non-allowlisted key before I/O.

        Inspector/Jobs use ``mutate_document`` for optimistic preconditions,
        permission checks and combined writes. This primitive shares their lock.
        """
        validated = core_payload(deepcopy(payload))
        if not validated:
            raise PaperlessValidationError("An empty mutation is not allowed.")
        async with self.coordinator.hold(document_id):
            response = await self._request(
                "PATCH", f"/api/documents/{document_id}/", json=validated
            )
            return Document.model_validate(response.json())

    async def update_custom_fields(
        self,
        document_id: int,
        updates: list[CustomFieldValue] | list[dict[str, Any]],
        *,
        expected_before: list[CustomFieldValue] | None = None,
        acknowledge_external_race: bool = False,
    ) -> Document:
        """Complete merge under the shared lock; never externally atomic.

        Low-level compatibility helper for verified callers. Explicit race
        acknowledgement is mandatory. Inspector/Jobs use ``mutate_document``.
        """
        copied = self._validate_custom_updates(updates)
        self._require_race_ack(acknowledge_external_race)
        async with self.coordinator.hold(document_id):
            current = await self.get_document(document_id)
            if expected_before is not None:
                actual = sorted(
                    [item.model_dump() for item in current.custom_fields], key=lambda x: x["field"]
                )
                expected = sorted(
                    [item.model_dump() for item in expected_before], key=lambda x: x["field"]
                )
                if json_module.dumps(actual, sort_keys=True) != json_module.dumps(
                    expected, sort_keys=True
                ):
                    raise PaperlessConflictError("Custom fields changed; reload before saving.")
            merged = merge_custom_fields(current.custom_fields, copied)
            response = await self._request(
                "PATCH", f"/api/documents/{document_id}/", json={"custom_fields": merged}
            )
            return Document.model_validate(response.json())

    @staticmethod
    def _require_race_ack(acknowledged: bool) -> None:
        if acknowledged is not True:
            raise PaperlessValidationError(
                "Custom-field writes require acknowledgement of the external-writer race. "
                "Pause other writers; Paperless does not provide an atomic precondition."
            )

    @staticmethod
    def _validate_custom_updates(
        updates: list[CustomFieldValue] | list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        copied = deepcopy(
            [
                item.model_dump() if isinstance(item, CustomFieldValue) else dict(item)
                for item in updates
            ]
        )
        seen: set[int] = set()
        for item in copied:
            if set(item) != {"field", "value"}:
                raise PaperlessValidationError("Custom updates require exactly field and value.")
            field = item["field"]
            if isinstance(field, str) and field.isdecimal():
                field = int(field)
            if type(field) is not int or field <= 0 or field in seen:
                raise PaperlessValidationError("Custom field IDs must be unique positive integers.")
            item["field"] = field
            seen.add(field)
        return copied

    async def mutate_document(
        self,
        document_id: int,
        *,
        expected_revision: str,
        core: dict[str, Any],
        custom_updates: list[dict[str, Any]],
        remove_custom_fields: list[int],
        acknowledge_external_race: bool = False,
    ) -> MutationResult:
        """The coordinated per-document write boundary for Inspector and Jobs.

        One fresh read, permission/precondition check, complete custom merge,
        one PATCH. All cooperating actors hold the same lock for that cycle.
        No retry and no rollback; the PATCH response supplies actual values.
        """
        plan = MutationPlan(core, custom_updates, remove_custom_fields, acknowledge_external_race)
        # Preserve M5's validation-before-I/O contract.
        self._validate_plan(plan)

        def prepare(current: Document) -> MutationPlan:
            if revision(current) != expected_revision:
                raise PaperlessConflictError(
                    "Document changed since it was opened; reload and review before saving.",
                    details={"document_id": document_id, "phase": "before_write"},
                )
            return plan

        result = await self.mutate_document_with_plan(document_id, prepare)
        assert result is not None
        return result

    def _validate_plan(
        self, plan: MutationPlan
    ) -> tuple[dict[str, Any], list[dict[str, Any]], list[int]]:
        payload = core_payload(deepcopy(plan.core))
        updates = self._validate_custom_updates(plan.custom_updates)
        removals = list(plan.remove_custom_fields)
        if any(type(i) is not int or i <= 0 for i in removals) or len(set(removals)) != len(
            removals
        ):
            raise PaperlessValidationError("Removal IDs must be unique positive integers.")
        if set(removals) & {item["field"] for item in updates}:
            raise PaperlessValidationError("A field cannot be both set and removed.")
        if not payload and not updates and not removals:
            raise PaperlessValidationError("An empty mutation is not allowed.")
        if updates or removals:
            self._require_race_ack(plan.acknowledge_external_race)
        return payload, updates, removals

    async def mutate_document_with_plan(
        self, document_id: int, prepare: Callable[[Document], MutationPlan | None]
    ) -> MutationResult | None:
        """Shared M5/M8 boundary; planning and durable intent run under its lock.

        Returning no plan means no write. The pre-send callback is synchronous:
        it commits intent and checks ownership without adding a network window.
        """
        async with self.coordinator.hold(document_id):
            current = await self.get_document(document_id)
            if current.id != document_id:
                raise PaperlessConflictError("Paperless returned a different document ID.")
            if current.deleted_at is not None:
                raise PaperlessNotFoundError("Document has been deleted or moved to trash.")
            if current.user_can_change is not True:
                raise PaperlessForbiddenError(
                    "This document is not confirmed editable.", status_code=403
                )
            plan = prepare(current)
            if plan is None:
                return None
            payload, updates, removals = self._validate_plan(plan)
            if updates or removals:
                payload["custom_fields"] = [
                    item
                    for item in merge_custom_fields(current.custom_fields, updates)
                    if item["field"] not in removals
                ]
            if plan.before_send is not None:
                plan.before_send()
            response = await self._request("PATCH", f"/api/documents/{document_id}/", json=payload)
            if plan.after_response is not None:
                plan.after_response()
            acknowledged = Document.model_validate(response.json())
            written = await self.get_document(document_id) if plan.readback else acknowledged
            return MutationResult(
                before=current, intended=payload, written=written, response=acknowledged
            )
