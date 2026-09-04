"""Unit tests for the live-test safety gate itself.

The gate decides whether a suite that CREATES, MODIFIES and DELETES documents
is allowed to run, and against which host. It is therefore the one piece of
test infrastructure that deserves its own tests: a bug here does not fail a
build, it damages somebody's library.

These are unit tests on purpose. They must run in the default suite, on every
CI job, without a Paperless anywhere near them.
"""

from __future__ import annotations

import pytest

from tests.backend.live import conftest as gate

# ``pytest.fail`` raises ``Failed``, which derives from ``BaseException`` and
# NOT from ``Exception``. That is a feature here, not a detail: a refusal to
# run destructive tests cannot be swallowed by a stray ``except Exception``
# anywhere in a fixture chain. Assert on the precise type so that a future
# rewrite of the guard into a plain ``raise RuntimeError`` - which would be
# catchable - fails this test loudly.
GuardRefusal = pytest.fail.Exception


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (gate.ALLOW_ENV, *gate.URL_ENVS, *gate.TOKEN_ENVS):
        monkeypatch.delenv(name, raising=False)


class TestOptIn:
    @pytest.mark.parametrize("value", ["true", "TRUE", "True", "1", "yes"])
    def test_accepts_the_documented_affirmatives(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        monkeypatch.setenv(gate.ALLOW_ENV, value)
        assert gate.live_tests_allowed() is True

    @pytest.mark.parametrize("value", ["", "false", "0", "no", "maybe", " "])
    def test_refuses_everything_else(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        monkeypatch.setenv(gate.ALLOW_ENV, value)
        assert gate.live_tests_allowed() is False

    def test_unset_means_disabled(self) -> None:
        # The default must be the safe one. Anyone cloning the repository and
        # running the suite must not write to anything.
        assert gate.live_tests_allowed() is False


class TestHostAllowlist:
    @pytest.mark.parametrize(
        "url",
        [
            "http://localhost:8010",
            "http://127.0.0.1:8010",
            "http://paperless:8000",
            "https://LOCALHOST:8010",
        ],
    )
    def test_authorised_hosts_pass(self, url: str) -> None:
        gate.assert_authorised_target(url)

    @pytest.mark.parametrize(
        "url",
        [
            # The substring trap: every one of these contains an authorised
            # host as a substring, and every one is somebody else's server.
            "https://paperless.example.com",
            "https://localhost.evil.example",
            "https://127.0.0.1.example.com",
            "https://my-paperless.home.arpa",
            "https://docs.example.org",
        ],
    )
    def test_foreign_hosts_are_refused(self, url: str) -> None:
        with pytest.raises(GuardRefusal, match="Refusing to run destructive"):
            gate.assert_authorised_target(url)


class TestTargetSelection:
    """Regression tests for a defect found while testing the gate itself.

    Only ``PAPERWRENCH_LIVE_PAPERLESS_URL`` used to be read. Exporting the
    application's own ``PAPERWRENCH_PAPERLESS_URL`` did nothing: the suite
    silently fell back to its localhost default and reported success, having
    tested an instance the operator never named. Silently ignoring the target
    of a destructive suite is the failure mode this pins shut.
    """

    def test_dedicated_variable_is_honoured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(gate.URL_ENVS[0], "http://127.0.0.1:9999")
        value, name = gate._first_env(gate.URL_ENVS)
        assert value == "http://127.0.0.1:9999"
        assert name == gate.URL_ENVS[0]

    def test_application_variable_is_honoured_too(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(gate.URL_ENVS[1], "http://127.0.0.1:8010")
        value, name = gate._first_env(gate.URL_ENVS)
        assert value == "http://127.0.0.1:8010"
        assert name == gate.URL_ENVS[1]

    def test_the_dedicated_variable_wins(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(gate.URL_ENVS[0], "http://127.0.0.1:1111")
        monkeypatch.setenv(gate.URL_ENVS[1], "http://127.0.0.1:2222")
        value, _ = gate._first_env(gate.URL_ENVS)
        assert value == "http://127.0.0.1:1111"

    def test_a_foreign_target_is_refused_through_either_spelling(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Whichever variable names the host, the allowlist must still apply.
        for name in gate.URL_ENVS:
            monkeypatch.delenv(gate.URL_ENVS[0], raising=False)
            monkeypatch.delenv(gate.URL_ENVS[1], raising=False)
            monkeypatch.setenv(name, "https://paperless.someone-real.example")
            value, _ = gate._first_env(gate.URL_ENVS)
            assert value is not None
            with pytest.raises(GuardRefusal, match="Refusing to run destructive"):
                gate.assert_authorised_target(value)

    def test_blank_values_do_not_count_as_a_target(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(gate.URL_ENVS[0], "   ")
        monkeypatch.setenv(gate.URL_ENVS[1], "http://127.0.0.1:8010")
        value, name = gate._first_env(gate.URL_ENVS)
        assert value == "http://127.0.0.1:8010"
        assert name == gate.URL_ENVS[1]

    def test_the_default_target_is_itself_authorised(self) -> None:
        # The fallback must never be able to reach a stranger.
        gate.assert_authorised_target(gate.DEFAULT_LIVE_URL)
