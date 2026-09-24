"""Expiring preview staging, separate from future Jobs.

Revision ID: 7c123a9b01ef
Revises: 8ac05542b36d
"""

import sqlalchemy as sa
from alembic import op

revision = "7c123a9b01ef"
down_revision = "8ac05542b36d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "previews",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ready", sa.Boolean(), nullable=False),
        sa.Column("confirmed", sa.Boolean(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("summary_json", sa.Text(), nullable=False),
    )
    op.create_index("ix_previews_expires_at", "previews", ["expires_at"])
    op.create_table(
        "preview_documents",
        sa.Column(
            "preview_id",
            sa.String(32),
            sa.ForeignKey("previews.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("position", sa.Integer(), primary_key=True),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.UniqueConstraint("preview_id", "document_id", name="preview_document_identity"),
    )
    op.create_index(
        "ix_preview_status_position", "preview_documents", ["preview_id", "status", "position"]
    )


def downgrade() -> None:
    op.drop_table("preview_documents")
    op.drop_table("previews")
