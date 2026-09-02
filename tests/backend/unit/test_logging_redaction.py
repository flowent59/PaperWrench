"""Secret redaction is a security control, so it is tested like one.

Covers the "no token leakage" critical test from the agreed test plan.
"""

from __future__ import annotations

from paperwrench.logging import MAX_VALUE_LENGTH
from paperwrench.logging import REDACTED
from paperwrench.logging import redaction_processor
from paperwrench.logging import register_secret


def _process(event: dict[str, object]) -> dict[str, object]:
    return dict(redaction_processor(None, "info", event))


def test_sensitive_keys_are_redacted() -> None:
    out = _process(
        {
            "event": "request",
            "authorization": "Token super-secret-value",
            "password": "hunter2",
            "token": "abc123def456",
            "safe": "keep me",
        }
    )
    assert out["authorization"] == REDACTED
    assert out["password"] == REDACTED
    assert out["token"] == REDACTED
    assert out["safe"] == "keep me"


def test_sensitive_keys_are_case_insensitive() -> None:
    out = _process({"event": "e", "Authorization": "Token x", "COOKIE": "a=b"})
    assert out["Authorization"] == REDACTED
    assert out["COOKIE"] == REDACTED


def test_registered_secret_is_scrubbed_from_any_value() -> None:
    """The token must not leak even via a non-sensitive key name."""
    register_secret("my-paperless-token-1234")
    out = _process({"event": "oops", "url": "http://p/api?x=my-paperless-token-1234"})
    assert "my-paperless-token-1234" not in str(out["url"])
    assert REDACTED in str(out["url"])


def test_nested_structures_are_redacted() -> None:
    register_secret("nested-secret-value-99")
    out = _process(
        {
            "event": "e",
            "request": {
                "headers": {"Authorization": "Token zzz"},
                "body": ["nested-secret-value-99", {"token": "q"}],
            },
        }
    )
    request = out["request"]
    assert isinstance(request, dict)
    assert request["headers"]["Authorization"] == REDACTED
    assert "nested-secret-value-99" not in str(request["body"])
    assert request["body"][1]["token"] == REDACTED


def test_short_values_are_not_registered_as_secrets() -> None:
    """Registering a tiny string would redact unrelated text everywhere."""
    register_secret("abc")
    out = _process({"event": "e", "msg": "abc def"})
    assert out["msg"] == "abc def"


def test_long_values_are_truncated() -> None:
    """Titles and field values can contain personal data."""
    out = _process({"event": "e", "title": "x" * (MAX_VALUE_LENGTH + 50)})
    title = out["title"]
    assert isinstance(title, str)
    assert title.endswith("...[truncated]")
    assert len(title) < MAX_VALUE_LENGTH + 50
