"""Store private, reusable filter sets."""

import sqlalchemy as sa
from alembic import op

revision = "8dbbc920eb54"
down_revision = "fa0e77b134ac"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "saved_filter_sets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("definition_json", sa.Text(), nullable=False),
        sa.UniqueConstraint("owner_id", "name", name="saved_filter_owner_name"),
    )
    op.create_index("ix_saved_filter_sets_owner_id", "saved_filter_sets", ["owner_id"])


def downgrade() -> None:
    op.drop_table("saved_filter_sets")
