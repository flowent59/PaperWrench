"""Migration preserves legacy evidence, cascades and ORM schema agreement."""

from pathlib import Path

from alembic import command
from sqlalchemy import inspect
from sqlalchemy import text

from paperwrench.db.engine import create_db_engine
from paperwrench.db.migrate import build_alembic_config


def test_m8_upgrade_legacy_targets_and_drift(tmp_path: Path) -> None:
    url = f"sqlite+pysqlite:///{tmp_path / 'migration.db'}"
    config = build_alembic_config(url)
    command.upgrade(config, "7c123a9b01ef")
    engine = create_db_engine(url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO jobs (id, type, status, title, document_ids_json, total_count, "
                "succeeded_count, failed_count, skipped_count, created_at) VALUES "
                "(1, 'transform', 'running', 'legacy', '[7, 8]', 2, 0, 0, 0, '2026-09-24')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO job_operations (job_id, document_id, field_kind, field_key, "
                "before_value_json, written_value_json, status, attempts) VALUES "
                "(1, 7, 'core', 'title', '\"old\"', '\"new\"', 'succeeded', 1)"
            )
        )
    command.upgrade(config, "head")
    command.check(config)
    with engine.begin() as connection:
        assert "document_ids_json" not in {
            c["name"] for c in inspect(connection).get_columns("jobs")
        }
        assert list(
            connection.execute(
                text("SELECT document_id, status FROM job_targets ORDER BY position")
            )
        ) == [(7, "ambiguous"), (8, "ambiguous")]
        assert connection.scalar(text("SELECT status FROM jobs")) == "interrupted"
        assert connection.scalar(text("SELECT owner_id FROM jobs")) is None
        assert connection.scalar(text("SELECT written_value_json FROM job_operations")) == '"new"'
        connection.execute(text("DELETE FROM jobs WHERE id=1"))
        assert connection.scalar(text("SELECT count(*) FROM job_targets")) == 0
        assert connection.scalar(text("SELECT count(*) FROM job_operations")) == 0
    command.downgrade(config, "7c123a9b01ef")
    command.upgrade(config, "head")
    command.check(config)
    engine.dispose()
