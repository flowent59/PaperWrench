"""Configuration behaviour, including secret handling."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from paperwrench.config import MAX_ALLOWED_CONCURRENCY
from paperwrench.config import SUPPORTED_PAPERLESS_API_VERSION
from paperwrench.config import Settings


def test_defaults_are_safe() -> None:
    s = Settings()
    assert s.paperless_configured is False
    assert s.paperless_api_version == SUPPORTED_PAPERLESS_API_VERSION
    assert s.max_concurrency == 4
    assert s.default_page_size == 100
    assert s.auto_resume_jobs is False
    assert s.cors_origins == []


def test_accept_header_pins_api_version() -> None:
    """We negotiate explicitly rather than relying on the server default."""
    s = Settings(PAPERWRENCH_PAPERLESS_API_VERSION=10)
    assert s.accept_header == "application/json; version=10"


def test_trailing_slash_is_stripped_from_url() -> None:
    s = Settings(PAPERLESS_URL="http://paperless:8000/")
    assert s.paperless_url == "http://paperless:8000"


def test_token_is_not_exposed_by_repr() -> None:
    """A stray log/repr of settings must not print the token."""
    s = Settings(PAPERLESS_TOKEN="super-secret-token")
    assert "super-secret-token" not in repr(s)
    assert "super-secret-token" not in str(s)
    assert s.paperless_token.get_secret_value() == "super-secret-token"


def test_token_file_takes_precedence(tmp_path: Path) -> None:
    """Docker secrets support."""
    token_file = tmp_path / "token"
    token_file.write_text("token-from-file\n", encoding="utf-8")
    s = Settings(PAPERLESS_TOKEN="inline", PAPERLESS_TOKEN_FILE=str(token_file))
    assert s.paperless_token.get_secret_value() == "token-from-file"


def test_missing_token_file_falls_back_to_env(tmp_path: Path) -> None:
    s = Settings(PAPERLESS_TOKEN="inline", PAPERLESS_TOKEN_FILE=str(tmp_path / "absent"))
    assert s.paperless_token.get_secret_value() == "inline"


def test_paperless_configured_requires_url_and_token() -> None:
    assert Settings(PAPERLESS_URL="http://p").paperless_configured is False
    assert Settings(PAPERLESS_TOKEN="t").paperless_configured is False
    assert Settings(PAPERLESS_URL="http://p", PAPERLESS_TOKEN="t").paperless_configured is True


def test_concurrency_is_hard_capped() -> None:
    """Users must not be able to DoS their own Paperless from a config file."""
    with pytest.raises(ValidationError):
        Settings(PAPERWRENCH_MAX_CONCURRENCY=MAX_ALLOWED_CONCURRENCY + 1)
    with pytest.raises(ValidationError):
        Settings(PAPERWRENCH_MAX_CONCURRENCY=0)


def test_page_size_is_capped() -> None:
    with pytest.raises(ValidationError):
        Settings(PAPERWRENCH_DEFAULT_PAGE_SIZE=1000)


def test_api_version_below_minimum_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(PAPERWRENCH_PAPERLESS_API_VERSION=8)


def test_cors_origins_accepts_comma_separated_string() -> None:
    s = Settings(PAPERWRENCH_CORS_ORIGINS="http://localhost:5173, http://localhost:3000")
    assert s.cors_origins == ["http://localhost:5173", "http://localhost:3000"]


def test_sqlite_path_is_derived() -> None:
    s = Settings(PAPERWRENCH_DATABASE_URL="sqlite+pysqlite:///./data/pw.db")
    assert s.sqlite_path == Path("./data/pw.db")
