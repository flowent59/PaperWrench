"""Add per-user ownership without adopting legacy rows.

Revision ID: d2f3a401b812
Revises: c814b207f001
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d2f3a401b812"
down_revision: str | None = "c814b207f001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable is intentional. Existing v0.1 rows remain unowned and therefore
    # invisible; silently assigning them to the first person to sign in would
    # grant that person another operator's history and document targets.
    # SQLite implements the constraint change by replacing the parent table.
    # Dropping that temporary parent triggers ON DELETE CASCADE even though the
    # logical collection survives, so preserve memberships across the rewrite.
    op.execute(
        "CREATE TEMP TABLE pw_collection_documents_backup AS "
        "SELECT collection_id, document_id, added_at FROM collection_documents"
    )
    with op.batch_alter_table("collections") as batch:
        batch.add_column(sa.Column("owner_id", sa.Integer(), nullable=True))
        batch.drop_constraint("uq_collections_name", type_="unique")
        batch.create_unique_constraint("collection_owner_name", ["owner_id", "name"])
        batch.create_index("ix_collections_owner_id", ["owner_id"])
    op.execute(
        "INSERT OR IGNORE INTO collection_documents (collection_id, document_id, added_at) "
        "SELECT collection_id, document_id, added_at FROM pw_collection_documents_backup"
    )
    op.execute("DROP TABLE pw_collection_documents_backup")
    with op.batch_alter_table("schemas") as batch:
        batch.add_column(sa.Column("owner_id", sa.Integer(), nullable=True))
        batch.drop_constraint("uq_schemas_name", type_="unique")
        batch.create_unique_constraint("schema_owner_name", ["owner_id", "name"])
        batch.create_index("ix_schemas_owner_id", ["owner_id"])
    with op.batch_alter_table("previews") as batch:
        batch.add_column(sa.Column("owner_id", sa.Integer(), nullable=True))
        batch.create_index("ix_previews_owner_id", ["owner_id"])
    with op.batch_alter_table("jobs") as batch:
        batch.add_column(sa.Column("owner_id", sa.Integer(), nullable=True))
        batch.create_index("ix_jobs_owner_id", ["owner_id"])
        batch.create_index("ix_jobs_owner_created", ["owner_id", "created_at"])


def downgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.drop_index("ix_jobs_owner_created")
        batch.drop_index("ix_jobs_owner_id")
        batch.drop_column("owner_id")
    with op.batch_alter_table("previews") as batch:
        batch.drop_index("ix_previews_owner_id")
        batch.drop_column("owner_id")
    with op.batch_alter_table("schemas") as batch:
        batch.drop_index("ix_schemas_owner_id")
        batch.drop_constraint("schema_owner_name", type_="unique")
        batch.create_unique_constraint("uq_schemas_name", ["name"])
        batch.drop_column("owner_id")
    op.execute(
        "CREATE TEMP TABLE pw_collection_documents_backup AS "
        "SELECT collection_id, document_id, added_at FROM collection_documents"
    )
    with op.batch_alter_table("collections") as batch:
        batch.drop_index("ix_collections_owner_id")
        batch.drop_constraint("collection_owner_name", type_="unique")
        batch.create_unique_constraint("uq_collections_name", ["name"])
        batch.drop_column("owner_id")
    op.execute(
        "INSERT OR IGNORE INTO collection_documents (collection_id, document_id, added_at) "
        "SELECT collection_id, document_id, added_at FROM pw_collection_documents_backup"
    )
    op.execute("DROP TABLE pw_collection_documents_backup")
