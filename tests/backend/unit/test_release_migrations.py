"""Fresh install and a populated M12 backup restored into the release schema."""

import sqlite3
from pathlib import Path

from alembic import command
from sqlalchemy import text

from paperwrench.db.engine import create_db_engine
from paperwrench.db.migrate import build_alembic_config


def test_m12_backup_restore_and_idempotent_release_upgrade(tmp_path: Path) -> None:
    original = tmp_path / "m12.db"
    url = f"sqlite+pysqlite:///{original}"
    config = build_alembic_config(url)
    command.upgrade(config, "c814b207f001")  # M12 head, also the M13 head
    engine = create_db_engine(url)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO collections (id, name, kind, created_at, updated_at) "
            "VALUES (1, 'Vacations', 'static', '2026-01-01', '2026-01-01')"
        )
        connection.exec_driver_sql(
            "INSERT INTO collection_documents (collection_id, document_id, added_at) "
            "VALUES (1, 42, '2026-01-01')"
        )
        connection.exec_driver_sql(
            "INSERT INTO schemas (id, name, applies_when_json, rules_json, created_at, updated_at) "
            "VALUES (1, 'Amounts', '{\"version\":1,\"query\":{}}', "
            "'{\"version\":1,\"items\":[]}', '2026-01-01', '2026-01-01')"
        )
    # The documented SQLite backup API includes committed WAL pages.
    restored = tmp_path / "restored.db"
    with sqlite3.connect(original) as source, sqlite3.connect(restored) as destination:
        source.backup(destination)
    engine.dispose()
    restored_url = f"sqlite+pysqlite:///{restored}"
    restored_config = build_alembic_config(restored_url)
    command.upgrade(restored_config, "head")
    command.upgrade(restored_config, "head")
    command.check(restored_config)
    restored_engine = create_db_engine(restored_url)
    with restored_engine.begin() as connection:
        assert connection.scalar(text("SELECT name FROM collections")) == "Vacations"
        assert connection.scalar(text("SELECT document_id FROM collection_documents")) == 42
        assert connection.scalar(text("SELECT rules_json FROM schemas")) == (
            '{"version":1,"items":[]}'
        )
        assert connection.scalar(text("SELECT owner_id FROM collections")) is None
        assert connection.scalar(text("SELECT owner_id FROM schemas")) is None
    with sqlite3.connect(restored) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    restored_engine.dispose()
