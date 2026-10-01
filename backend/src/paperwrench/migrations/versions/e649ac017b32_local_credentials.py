"""Optional encrypted local credentials.

Revision ID: e649ac017b32
Revises: c39d582ba7e4
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e649ac017b32"
down_revision: str | None = "c39d582ba7e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "paperless_identities",
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("upstream_user_id", sa.Integer(), nullable=False),
        sa.Column("paperless_url", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("owner_id"),
        sa.UniqueConstraint("paperless_url", "upstream_user_id"),
    )
    op.create_table(
        "local_credentials",
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(255), nullable=False),
        sa.Column("paperless_url", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("encrypted_token", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("owner_id"),
        sa.UniqueConstraint("username"),
    )


def downgrade() -> None:
    op.drop_table("local_credentials")
    op.drop_table("paperless_identities")
