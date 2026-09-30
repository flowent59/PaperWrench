"""Add private saved rules, revisions and execution provenance.

Revision ID: a50b9d134e71
Revises: ce46a07b9123
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "a50b9d134e71"
down_revision: str | None = "ce46a07b9123"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_targets_document_id", "job_targets", ["document_id"])
    op.create_table(
        "saved_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("definition_json", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("owner_id", "name", name="rule_owner_name"),
    )
    op.create_index("ix_saved_rules_owner_id", "saved_rules", ["owner_id"])
    op.create_table(
        "saved_rule_revisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("rule_id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("definition_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("rule_id", "revision", name="rule_revision_identity"),
    )
    op.create_index("ix_saved_rule_revisions_rule_id", "saved_rule_revisions", ["rule_id"])
    with op.batch_alter_table("previews") as batch:
        batch.add_column(sa.Column("rule_id", sa.Integer()))
        batch.add_column(sa.Column("rule_revision", sa.Integer()))
        batch.add_column(sa.Column("rule_spec_json", sa.Text()))
        batch.create_index("ix_previews_rule_id", ["rule_id"])
    with op.batch_alter_table("jobs") as batch:
        batch.add_column(sa.Column("rule_id", sa.Integer()))
        batch.add_column(sa.Column("rule_revision", sa.Integer()))
        batch.add_column(sa.Column("rule_name", sa.String(255)))
        batch.create_index("ix_jobs_rule_id", ["rule_id"])


def downgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.drop_index("ix_jobs_rule_id")
        batch.drop_column("rule_name")
        batch.drop_column("rule_revision")
        batch.drop_column("rule_id")
    with op.batch_alter_table("previews") as batch:
        batch.drop_index("ix_previews_rule_id")
        batch.drop_column("rule_spec_json")
        batch.drop_column("rule_revision")
        batch.drop_column("rule_id")
    op.drop_index("ix_saved_rule_revisions_rule_id", table_name="saved_rule_revisions")
    op.drop_table("saved_rule_revisions")
    op.drop_index("ix_saved_rules_owner_id", table_name="saved_rules")
    op.drop_table("saved_rules")
    op.drop_index("ix_targets_document_id", table_name="job_targets")
