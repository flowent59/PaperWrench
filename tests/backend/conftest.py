"""Shared test fixtures."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.orm import Session

from paperwrench.analytics import DashboardCache
from paperwrench.api.deps import get_auth_session
from paperwrench.auth.service import AuthSession
from paperwrench.config import Settings
from paperwrench.db.base import Base
from paperwrench.db.engine import dispose_engine
from paperwrench.db.engine import init_engine
from paperwrench.logging import reset_secrets
from paperwrench.paperless import MetadataRegistry
from paperwrench.paperless import PaperlessClient


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
def auth_record(settings: Settings) -> AuthSession:
    """Mutable authenticated identity used by API contract tests."""
    paperless = PaperlessClient(settings)
    return AuthSession(
        session_id="test-session",
        paperless_user_id=1,
        username="test-user",
        display_name="Test User",
        csrf_token="test-csrf",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        validated_at=datetime.now(UTC),
        client=paperless,
        registry=MetadataRegistry(paperless),
        dashboard_cache=DashboardCache(),
    )


@pytest.fixture
def client(settings: Settings, auth_record: AuthSession) -> Iterator[TestClient]:
    """A TestClient running the real lifespan (engine + runtime lock)."""
    from paperwrench.main import create_app

    app = create_app(settings)
    record = auth_record
    app.dependency_overrides[get_auth_session] = lambda: record
    with TestClient(app) as test_client:
        yield test_client
    dispose_engine()


@pytest.fixture
def unauthenticated_client(settings: Settings) -> Iterator[TestClient]:
    """A real app with no dependency bypass, used by authentication tests."""
    from paperwrench.main import create_app

    app = create_app(settings.model_copy(update={"paperless_token": SecretStr("")}))
    with TestClient(app) as test_client:
        yield test_client
    dispose_engine()
