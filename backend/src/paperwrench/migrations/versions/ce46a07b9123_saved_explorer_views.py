"""Add private saved Explorer views.

Revision ID: ce46a07b9123
Revises: f43a91c02e17
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "ce46a07b9123"
down_revision: str | None = "f43a91c02e17"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "saved_explorer_views",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("definition_json", sa.Text(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_saved_explorer_views")),
        sa.UniqueConstraint("owner_id", "name", name="saved_view_owner_name"),
    )
    op.create_index(
        "ix_saved_explorer_views_owner_id", "saved_explorer_views", ["owner_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_saved_explorer_views_owner_id", table_name="saved_explorer_views")
    op.drop_table("saved_explorer_views")
