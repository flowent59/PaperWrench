"""Programmatic Alembic upgrade.

PaperWrench ships as a single container that a self-hoster starts with
`docker compose up`; asking them to run a migration command by hand before the
app boots would be a footgun. Migrations therefore run inside the application
lifespan.

They must run *before* the single-instance runtime lock is acquired, because
the lock itself lives in a table that a migration creates. Concurrent first
boots are serialised by SQLite's own write lock plus `busy_timeout`: the loser
blocks, then finds the schema already at `head` and applies nothing. The
application-level lock takes over immediately afterwards (ADR-0006).

The revision scripts live *inside* the package (`paperwrench/migrations`) so
they are shipped by the wheel and resolvable from an installed distribution,
not only from a source checkout.
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

from paperwrench.logging import get_logger

logger = get_logger(__name__)

# .../paperwrench/db/migrate.py -> .../paperwrench
PACKAGE_ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = PACKAGE_ROOT / "migrations"


def build_alembic_config(database_url: str) -> Config:
    """Build an Alembic config pointing at the running instance's database."""
    # No alembic.ini on purpose: everything Alembic needs is set here, so an
    # installed wheel behaves exactly like a source checkout.
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", database_url)
    # Silence Alembic's own logging config; structlog owns the output.
    config.attributes["configure_logger"] = False
    return config


def run_migrations(database_url: str) -> None:
    """Upgrade the database to `head`.

    Raises if the migration fails: refusing to serve is much safer than
    serving against a schema we do not understand.
    """
    if not MIGRATIONS_DIR.is_dir():  # pragma: no cover - packaging safety net
        logger.warning("migrations_directory_missing", path=str(MIGRATIONS_DIR))
        return

    logger.info("migrations_starting")
    command.upgrade(build_alembic_config(database_url), "head")
    logger.info("migrations_applied")
