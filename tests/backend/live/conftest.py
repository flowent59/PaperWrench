"""Gating for tests that write to a REAL Paperless-ngx instance.

Two independent locks must both be open before a single live test runs:

1. ``PAPERWRENCH_ALLOW_LIVE_TESTS=true`` - an explicit, deliberate opt-in.
2. The target URL must resolve to an explicitly authorised development host.

Either lock alone would be too weak. The opt-in alone would let someone run
the suite against production by exporting one variable; the allowlist alone
would let a stray ``PAPERLESS_URL=http://localhost`` tunnelled to a real
server through. Both together mean a destructive test requires intent *and*
a throwaway target.

These tests create, modify and delete data. They must never point at a
library anyone cares about.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from collections.abc import Iterator
from urllib.parse import urlsplit

import httpx
import pytest
import pytest_asyncio

from paperwrench.config import Settings
from paperwrench.paperless import PaperlessClient

# Exact hostnames only. A substring test would accept
# "paperless.someones-real-domain.example" - the precise accident this exists
# to prevent (the same bug was already caught once in the seed script).
AUTHORISED_LIVE_HOSTS = frozenset(
    {
        "localhost",
        "127.0.0.1",
        "::1",
        "paperless",  # docker-compose.dev.yml service name
        "host.docker.internal",
    }
)

ALLOW_ENV = "PAPERWRENCH_ALLOW_LIVE_TESTS"

# Two accepted spellings, in priority order, for both the URL and the token.
#
# The dedicated ``..._LIVE_...`` name exists so a developer can point the live
# suite somewhere without disturbing the application settings they already have
# exported. The application's own variable is accepted as a fallback because it
# is the obvious thing to reach for.
#
# Accepting only the first spelling was a real defect, caught while testing the
# guard itself: exporting PAPERWRENCH_PAPERLESS_URL had NO effect, the suite
# silently fell back to its localhost default, ran happily against a completely
# different instance, and reported 46 passed. A destructive test suite that
# ignores the target it was given - and says nothing - is worse than one that
# refuses to start.
URL_ENVS = ("PAPERWRENCH_LIVE_PAPERLESS_URL", "PAPERWRENCH_PAPERLESS_URL")
TOKEN_ENVS = ("PAPERWRENCH_LIVE_PAPERLESS_TOKEN", "PAPERWRENCH_PAPERLESS_TOKEN")

#: Used only when no variable is set at all. It is on the allowlist, so the
#: worst case is "ran against the local sandbox", never "ran against yours".
DEFAULT_LIVE_URL = "http://localhost:8010"


def _first_env(names: tuple[str, ...]) -> tuple[str | None, str | None]:
    """Return the first non-empty value among ``names``, and which one it was."""
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value, name
    return None, None

# Beyond this, the target is not a disposable sandbox.
MAX_SANDBOX_DOCUMENTS = 500


def live_tests_allowed() -> bool:
    return os.environ.get(ALLOW_ENV, "").strip().lower() in {"1", "true", "yes"}


def assert_authorised_target(url: str) -> None:
    host = (urlsplit(url).hostname or "").lower()
    if host not in AUTHORISED_LIVE_HOSTS:
        pytest.fail(
            f"Refusing to run destructive live tests against host {host!r}. "
            f"Authorised hosts: {', '.join(sorted(AUTHORISED_LIVE_HOSTS))}."
        )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Skip the whole live directory unless the opt-in is set."""
    if live_tests_allowed():
        return
    skip = pytest.mark.skip(
        reason=f"live Paperless tests disabled; set {ALLOW_ENV}=true to enable"
    )
    for item in items:
        if "/live/" in str(item.fspath).replace("\\", "/"):
            item.add_marker(skip)


@pytest.fixture(scope="session")
def live_url() -> str:
    value, _ = _first_env(URL_ENVS)
    url = (value or DEFAULT_LIVE_URL).rstrip("/")
    assert_authorised_target(url)
    return url


@pytest.fixture(scope="session")
def live_token(live_url: str) -> str:
    token, _ = _first_env(TOKEN_ENVS)
    if token:
        return token
    # Convenience for the dev stack, whose credentials are public by design.
    try:
        response = httpx.post(
            f"{live_url}/api/token/",
            json={"username": "admin", "password": "admin"},
            timeout=10.0,
        )
        response.raise_for_status()
        return str(response.json()["token"])
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"no live Paperless token available ({exc})")


@pytest.fixture(scope="session")
def live_settings(live_url: str, live_token: str) -> Settings:
    return Settings(
        PAPERLESS_URL=live_url,
        PAPERLESS_TOKEN=live_token,
        PAPERWRENCH_DATABASE_URL="sqlite+pysqlite:///:memory:",
    )


@pytest.fixture(scope="session", autouse=True)
def _guard_not_a_real_library(live_settings: Settings, live_url: str) -> Iterator[None]:
    """Last safety net: a big library is not a sandbox."""
    if not live_tests_allowed():
        yield
        return
    response = httpx.get(
        f"{live_url}/api/documents/",
        headers={
            "Authorization": f"Token {live_settings.paperless_token.get_secret_value()}",
            "Accept": "application/json; version=10",
        },
        params={"page_size": 1},
        timeout=10.0,
    )
    response.raise_for_status()
    count = int(response.json().get("count", 0))
    if count > MAX_SANDBOX_DOCUMENTS:
        pytest.fail(
            f"Refusing to run destructive tests: target holds {count} documents, "
            f"which is more than a sandbox should ({MAX_SANDBOX_DOCUMENTS})."
        )
    yield


@pytest_asyncio.fixture
async def live_client(live_settings: Settings) -> AsyncIterator[PaperlessClient]:
    async with PaperlessClient(live_settings) as client:
        yield client


@pytest_asyncio.fixture
async def raw_live(live_settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    """A raw httpx client, for probing behaviours the client abstracts away.

    Only live tests may do this: they are documenting the server's behaviour,
    not using it.
    """
    async with httpx.AsyncClient(
        base_url=live_settings.paperless_url,
        headers={
            "Authorization": f"Token {live_settings.paperless_token.get_secret_value()}",
            "Accept": "application/json; version=10",
        },
        timeout=30.0,
    ) as client:
        yield client


@pytest_asyncio.fixture
async def scratch_document(raw_live: httpx.AsyncClient) -> AsyncIterator[int]:
    """A disposable document, deleted afterwards.

    Live tests must not depend on the golden dataset staying pristine, and
    must not corrupt it either.
    """
    import asyncio
    import uuid

    marker = f"pw-live-{uuid.uuid4().hex[:8]}"
    files = {"document": (f"{marker}.txt", f"Contenu de test {marker}".encode(), "text/plain")}
    response = await raw_live.post(
        "/api/documents/post_document/", files=files, data={"title": marker}
    )
    response.raise_for_status()

    document_id: int | None = None
    for _ in range(60):
        listing = await raw_live.get("/api/documents/", params={"title__icontains": marker})
        listing.raise_for_status()
        results = listing.json().get("results", [])
        if results:
            document_id = int(results[0]["id"])
            break
        await asyncio.sleep(2)

    if document_id is None:
        pytest.skip("Paperless did not consume the scratch document in time")

    try:
        yield document_id
    finally:
        await raw_live.delete(f"/api/documents/{document_id}/")
