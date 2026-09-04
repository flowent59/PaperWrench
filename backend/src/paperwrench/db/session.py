"""Session helpers and the FastAPI dependency."""

from __future__ import annotations

from collections.abc import Generator
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy.orm import Session

from paperwrench.db.engine import get_session_factory


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope for non-request code (job worker, startup tasks)."""
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a request-scoped session."""
    factory = get_session_factory()
    session = factory()
    try:
        yield session
    finally:
        session.close()
