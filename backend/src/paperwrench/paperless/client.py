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

import types
from typing import Any
from typing import Self
from urllib.parse import urlsplit

import httpx
import structlog

from paperwrench.config import Settings
from paperwrench.errors import PaperlessForbiddenError
from paperwrench.errors import PaperlessIncompatibleError
from paperwrench.errors import PaperlessNotConfiguredError
from paperwrench.errors import PaperlessUnauthorizedError
from paperwrench.errors import PaperlessUnreachableError
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

logger = structlog.get_logger(__name__)

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

    def __init__(self, settings: Settings, *, http_client: httpx.AsyncClient | None = None) -> None:
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
                "Paperless authenticated the request but refused it "
                f"({method} {path}).",
                details={"upstream_status": status},
            )
        if status == 406:
            # VERIFIED_LIVE: this is exactly how 3.1.2 signals an API version
            # it cannot serve.
            raise PaperlessIncompatibleError(
                f"Paperless cannot serve API version "
                f"{self._settings.paperless_api_version}.",
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

    async def iter_documents(
        self, *, params: dict[str, Any] | None = None, page_size: int | None = None
    ) -> list[Document]:
        raw = await self.iter_pages("/api/documents/", params=params, page_size=page_size)
        return [Document.model_validate(item) for item in raw]

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
        """PATCH a document with an explicit payload.

        **Do not put ``custom_fields`` in here** unless the list is already the
        complete desired state. Use :meth:`update_custom_fields`, which does
        the read-modify-write for you. This method exists for scalar fields
        (``title``, ``document_type``, ...) where PATCH is genuinely partial.
        """
        response = await self._request("PATCH", f"/api/documents/{document_id}/", json=payload)
        return Document.model_validate(response.json())

    async def update_custom_fields(
        self,
        document_id: int,
        updates: list[CustomFieldValue] | list[dict[str, Any]],
        *,
        expected_before: list[CustomFieldValue] | None = None,
    ) -> Document:
        """Safely change some custom fields, preserving the others.

        This is the read-modify-write primitive mandated by ADR-0004, and the
        reason a partial ``custom_fields`` PATCH must never be issued anywhere
        else. Sending only the changed field is a silent data-loss bug -
        VERIFIED_LIVE on 3.1.2, where doing so deleted four of five fields and
        still returned ``200 OK``.

        If ``expected_before`` is supplied, the document's current custom
        fields must still match it or the write is refused. That is the
        forward conflict check: something changed the document between the
        preview and the write, so the merge would be computed from a stale
        base.
        """
        current = await self.get_document(document_id)

        if expected_before is not None:
            before = {item.field: item.value for item in current.custom_fields}
            expected = {item.field: item.value for item in expected_before}
            if before != expected:
                raise PaperlessConflictError(
                    f"Document {document_id} changed since it was read; "
                    "refusing to write from a stale base.",
                    details={"document_id": document_id},
                )

        merged = merge_custom_fields(current.custom_fields, updates)
        response = await self._request(
            "PATCH", f"/api/documents/{document_id}/", json={"custom_fields": merged}
        )
        return Document.model_validate(response.json())
