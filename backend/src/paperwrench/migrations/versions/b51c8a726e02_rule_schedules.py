"""Add approved schedules and unique execution occurrences.

Revision ID: b51c8a726e02
Revises: a50b9d134e71
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "b51c8a726e02"
down_revision: str | None = "a50b9d134e71"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rule_schedules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("rule_id", sa.Integer(), nullable=False, unique=True),
        sa.Column("rule_revision", sa.Integer(), nullable=False),
        sa.Column("approved_spec_json", sa.Text(), nullable=False),
        sa.Column("approval_preview_id", sa.String(32), nullable=False),
        sa.Column("approved_at", sa.DateTime(), nullable=False),
        sa.Column("recurrence_json", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("next_run_at", sa.DateTime(), nullable=False),
        sa.Column("acknowledge_external_race", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("notification", sa.String(80)),
    )
    op.create_index("ix_rule_schedules_owner_id", "rule_schedules", ["owner_id"])
    op.create_index("ix_rule_schedules_next_run_at", "rule_schedules", ["next_run_at"])
    op.create_table(
        "schedule_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("schedule_id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime()),
        sa.Column("rule_revision", sa.Integer(), nullable=False),
        sa.Column("approval_preview_id", sa.String(32), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("error_code", sa.String(80)),
        sa.Column("job_id", sa.Integer(), unique=True),
        sa.UniqueConstraint("schedule_id", "scheduled_for", name="schedule_occurrence"),
    )
    op.create_index("ix_schedule_runs_schedule_id", "schedule_runs", ["schedule_id"])


def downgrade() -> None:
    op.drop_table("schedule_runs")
    op.drop_table("rule_schedules")
