"""Add explicit per-schedule autonomous consent."""

import sqlalchemy as sa
from alembic import op

revision = "fa0e77b134ac"
down_revision = "e649ac017b32"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("rule_schedules", sa.Column(
        "autonomous", sa.Boolean(), nullable=False, server_default=sa.false(),
    ))


def downgrade() -> None:
    op.drop_column("rule_schedules", "autonomous")
