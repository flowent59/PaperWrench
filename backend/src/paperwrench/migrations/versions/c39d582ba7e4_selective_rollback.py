"""Index rollback provenance links for repeated partial restoration.

Revision ID: c39d582ba7e4
Revises: b51c8a726e02
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c39d582ba7e4"
down_revision: str | None = "b51c8a726e02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_job_operations_rollback_of_operation_id",
        "job_operations",
        ["rollback_of_operation_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_job_operations_rollback_of_operation_id", table_name="job_operations",
    )
