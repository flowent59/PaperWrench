"""Fresh install and a populated M12 backup restored into the release schema."""

import sqlite3
from pathlib import Path

from alembic import command
from sqlalchemy import select
from sqlalchemy.orm import Session

from paperwrench.db.engine import create_db_engine
from paperwrench.db.migrate import build_alembic_config
from paperwrench.db.models import Collection
from paperwrench.db.models import CollectionDocument
from paperwrench.db.models import DocumentSchema


def test_m12_backup_restore_and_idempotent_release_upgrade(tmp_path: Path) -> None:
    original = tmp_path / "m12.db"
    url = f"sqlite+pysqlite:///{original}"
    config = build_alembic_config(url)
    command.upgrade(config, "c814b207f001")  # M12 head, also the M13 head
    engine = create_db_engine(url)
    with Session(engine) as db:
        collection = Collection(name="Vacations", documents=[CollectionDocument(document_id=42)])
        schema = DocumentSchema(name="Amounts", applies_when_json='{"version":1,"query":{}}',
                                rules_json='{"version":1,"items":[]}')
        db.add_all([collection, schema])
        db.commit()
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
    with Session(restored_engine) as db:
        assert db.scalar(select(Collection.name)) == "Vacations"
        assert db.scalar(select(CollectionDocument.document_id)) == 42
        assert db.scalar(select(DocumentSchema.rules_json)) == '{"version":1,"items":[]}'
    with sqlite3.connect(restored) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    restored_engine.dispose()
