"""Durable document targets and preview handoff.

Revision ID: c814b207f001
Revises: 7c123a9b01ef
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from paperwrench.db.base import UtcDateTime

revision: str = "c814b207f001"
down_revision: str | None = "7c123a9b01ef"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("preview_id", sa.String(32), nullable=True))
    op.add_column("jobs", sa.Column("preview_summary_json", sa.Text(), nullable=True))
    op.add_column("jobs", sa.Column("source_kind", sa.String(16), nullable=True))
    op.add_column(
        "jobs",
        sa.Column(
            "acknowledge_external_race", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    # Index instead of a table rewrite: jobs already has dependent foreign keys.
    op.create_index("ix_jobs_preview_id", "jobs", ["preview_id"], unique=True)
    op.create_table(
        "job_targets",
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("preview_json", sa.Text(), nullable=True),
        sa.Column("execution_owner", sa.String(64), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("before_custom_fields_json", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("started_at", UtcDateTime(), nullable=True),
        sa.Column("finished_at", UtcDateTime(), nullable=True),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("job_id", "document_id"),
        sa.UniqueConstraint("job_id", "position", name="target_job_position"),
    )
    op.create_index("ix_targets_claim", "job_targets", ["job_id", "status", "position"])
    connection = op.get_bind()
    # M0-M7 never executed Jobs. Preserve any manually inserted legacy evidence
    # without inventing provenance or making it eligible for automatic execution.
    for row in connection.execute(sa.text("SELECT id, document_ids_json FROM jobs")):
        ids = dict.fromkeys(json.loads(row.document_ids_json))
        ids.update(
            dict.fromkeys(
                connection.execute(
                    sa.text("SELECT document_id FROM job_operations WHERE job_id=:id"),
                    {"id": row.id},
                ).scalars()
            )
        )
        for position, document_id in enumerate(ids):
            connection.execute(
                sa.text(
                    "INSERT INTO job_targets (job_id, document_id, position, status, attempts, error) "
                    "VALUES (:job, :doc, :pos, 'ambiguous', 0, 'LEGACY_MANUAL_REVIEW')"
                ),
                {"job": row.id, "doc": document_id, "pos": position},
            )
    connection.execute(
        sa.text("UPDATE jobs SET status='interrupted' WHERE status IN ('pending', 'running')")
    )
    connection.execute(sa.text(
        "UPDATE job_operations SET status='ambiguous', error='LEGACY_MANUAL_REVIEW'"
    ))
    # SQLite >=3.35 supports DROP COLUMN without rebuilding referenced tables.
    op.drop_column("jobs", "document_ids_json")


def downgrade() -> None:
    op.add_column(
        "jobs", sa.Column("document_ids_json", sa.Text(), nullable=False, server_default="[]")
    )
    connection = op.get_bind()
    job_id: int
    for job_id in connection.execute(sa.text("SELECT id FROM jobs")).scalars():
        ids: list[int] = list(
            connection.execute(
                sa.text("SELECT document_id FROM job_targets WHERE job_id=:id ORDER BY position"),
                {"id": job_id},
            ).scalars()
        )
        connection.execute(
            sa.text("UPDATE jobs SET document_ids_json=:ids WHERE id=:id"),
            {"id": job_id, "ids": json.dumps(ids)},
        )
    op.drop_table("job_targets")
    op.drop_index("ix_jobs_preview_id", table_name="jobs")
    for column in (
        "preview_id",
        "preview_summary_json",
        "source_kind",
        "acknowledge_external_race",
    ):
        op.drop_column("jobs", column)
