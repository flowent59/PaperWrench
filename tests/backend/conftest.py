"""Shared test fixtures."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from paperwrench.config import Settings
from paperwrench.db.base import Base
from paperwrench.db.engine import dispose_engine
from paperwrench.db.engine import init_engine
from paperwrench.logging import reset_secrets


@pytest.fixture(autouse=True)
def _clean_secrets() -> Iterator[None]:
    reset_secrets()
    yield
    reset_secrets()


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


@pytest.fixture
def settings(db_path: Path) -> Settings:
    """Settings pointing at a throwaway SQLite file."""
    return Settings(
        PAPERLESS_URL="http://paperless.test",
        PAPERLESS_TOKEN="test-token-abcdef123456",
        PAPERWRENCH_DATABASE_URL=f"sqlite+pysqlite:///{db_path}",
        PAPERWRENCH_LOG_FORMAT="console",
    )


@pytest.fixture
def session(settings: Settings) -> Iterator[Session]:
    """A session against a schema created directly from the ORM metadata."""
    engine = init_engine(settings.database_url)
    Base.metadata.create_all(engine)
    from paperwrench.db.engine import get_session_factory

    db = get_session_factory()()
    try:
        yield db
    finally:
        db.close()
        dispose_engine()


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    """A TestClient running the real lifespan (engine + runtime lock)."""
    from paperwrench.main import create_app

    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client
    dispose_engine()
