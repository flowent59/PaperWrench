"""Bounded retries for disposable users hitting Paperless's login throttle."""

from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from tests.backend.live import conftest as live


async def test_token_setup_respects_retry_after(
    respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    sleep = AsyncMock()
    monkeypatch.setattr("tests.backend.live.conftest.asyncio.sleep", sleep)
    route = respx_mock.post("http://paperless/api/token/").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "2"}),
            httpx.Response(200, json={"token": "disposable-token"}),
        ]
    )
    async with httpx.AsyncClient(base_url="http://paperless") as client:
        assert await live._obtain_test_token(client, "user", "password") == "disposable-token"
    assert route.call_count == 2
    sleep.assert_awaited_once_with(2)


@pytest.mark.parametrize(
    ("status", "retry_after"),
    [(401, "2"), (500, "2"), (429, None), (429, "invalid"), (429, "-1"), (429, "121")],
)
async def test_token_setup_does_not_retry_other_errors_or_unbounded_delays(
    respx_mock: respx.MockRouter,
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    retry_after: str | None,
) -> None:
    sleep = AsyncMock()
    monkeypatch.setattr("tests.backend.live.conftest.asyncio.sleep", sleep)
    headers = {} if retry_after is None else {"Retry-After": retry_after}
    route = respx_mock.post("http://paperless/api/token/").respond(status, headers=headers)
    async with httpx.AsyncClient(base_url="http://paperless") as client:
        with pytest.raises(httpx.HTTPStatusError) as exc:
            await live._obtain_test_token(client, "user", "password")
    assert exc.value.response.status_code == status
    assert route.call_count == 1
    sleep.assert_not_awaited()


async def test_token_setup_stops_after_three_attempts(
    respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    sleep = AsyncMock()
    monkeypatch.setattr("tests.backend.live.conftest.asyncio.sleep", sleep)
    route = respx_mock.post("http://paperless/api/token/").respond(
        429, headers={"Retry-After": "120"}
    )
    async with httpx.AsyncClient(base_url="http://paperless") as client:
        with pytest.raises(httpx.HTTPStatusError):
            await live._obtain_test_token(client, "user", "password")
    assert route.call_count == 3
    assert sleep.await_count == 2
    sleep.assert_awaited_with(120)
