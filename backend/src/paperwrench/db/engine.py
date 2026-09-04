"""SQLite engine setup.

SQLite is PaperWrench's own durable state. It holds no Paperless document
cache (see ADR-0001) - only PaperWrench concepts and the audit trail of
PaperWrench's own operations.

WAL mode matters for the Job Engine: the worker writes operation results while
HTTP requests read job progress concurrently. See ADR-0006.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import Engine
from sqlalchemy import create_engine
from sqlalchemy import event
from sqlalchemy.orm import Session
from sqlalchemy.orm import sessionmaker

from paperwrench.logging import get_logger

logger = get_logger(__name__)

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def _apply_sqlite_pragmas(dbapi_connection: Any, _connection_record: Any) -> None:
    """Apply the pragmas PaperWrench relies on, on every new connection."""
    cursor = dbapi_connection.cursor()
    try:
        # Concurrent reader while the job worker writes.
        cursor.execute("PRAGMA journal_mode=WAL")
        # Wait rather than immediately raising "database is locked".
        cursor.execute("PRAGMA busy_timeout=5000")
        # We rely on FK cascades for job_operations.
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA synchronous=NORMAL")
    finally:
        cursor.close()


def create_db_engine(database_url: str, *, echo: bool = False) -> Engine:
    """Create the engine, ensuring the parent directory exists for file DBs."""
    connect_args: dict[str, Any] = {}
    if database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        prefix = "sqlite+pysqlite:///"
        if database_url.startswith(prefix):
            raw = database_url[len(prefix) :]
            if raw and raw != ":memory:":
                Path(raw).parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(
        database_url,
        echo=echo,
        future=True,
        connect_args=connect_args,
        pool_pre_ping=True,
    )
    if database_url.startswith("sqlite"):
        event.listen(engine, "connect", _apply_sqlite_pragmas)
    return engine


def init_engine(database_url: str, *, echo: bool = False) -> Engine:
    """Initialise the process-wide engine and session factory."""
    global _engine, _session_factory
    _engine = create_db_engine(database_url, echo=echo)
    _session_factory = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    logger.info("database_initialised", dialect=_engine.dialect.name)
    return _engine


def get_engine() -> Engine:
    if _engine is None:  # pragma: no cover - defensive
        raise RuntimeError("Database engine not initialised; call init_engine() first")
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    if _session_factory is None:  # pragma: no cover - defensive
        raise RuntimeError("Session factory not initialised; call init_engine() first")
    return _session_factory


def dispose_engine() -> None:
    """Dispose of the engine (shutdown / tests)."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None
