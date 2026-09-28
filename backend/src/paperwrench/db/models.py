"""PaperWrench-owned ORM models.

Scope rule (ADR-0001): these tables hold PaperWrench concepts and the audit
trail of PaperWrench's own operations. They never mirror the Paperless library.

The only document data stored is *historical evidence* inside
``job_operations`` - a record of a past state at the moment we acted on it. It
is never read as current truth; it is only ever compared against freshly
fetched values.

Paperless document IDs are stored as plain integers with no foreign keys, so a
deletion in Paperless surfaces as a clean per-operation failure rather than a
corrupt join.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import CheckConstraint
from sqlalchemy import ForeignKey
from sqlalchemy import Index
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import Text
from sqlalchemy import UniqueConstraint
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column
from sqlalchemy.orm import relationship

from paperwrench.db.base import Base
from paperwrench.db.base import UtcDateTime
from paperwrench.db.base import utcnow

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class JobType(StrEnum):
    TRANSFORM = "transform"
    ROLLBACK = "rollback"


class JobStatus(StrEnum):
    """Execution lifecycle of a job (ADR-0006).

    Rollback is *not* a status: it is expressed relationally via
    ``Job.rollback_of_job_id``, so partial rollbacks are representable.
    """

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"
    #: Process died mid-run. Honest, and resumable - better than a job stuck
    #: at RUNNING forever.
    INTERRUPTED = "interrupted"

    @property
    def is_terminal(self) -> bool:
        return self in {
            JobStatus.COMPLETED,
            JobStatus.PARTIAL,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        }


class OperationStatus(StrEnum):
    """Outcome of a single document/field write."""

    PENDING = "pending"
    AMBIGUOUS = "ambiguous"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED_UNCHANGED = "skipped_unchanged"
    #: The value moved between preview and apply: never overwrite silently
    #: (forward conflict detection).
    SKIPPED_CONFLICT = "skipped_conflict"
    SKIPPED_PERMISSION = "skipped_permission"
    #: Document deleted or moved to trash between preview and apply.
    SKIPPED_MISSING = "skipped_missing"

    @property
    def is_terminal(self) -> bool:
        return self is not OperationStatus.PENDING


class FieldKind(StrEnum):
    CORE = "core"
    CUSTOM_FIELD = "custom_field"


class TargetStatus(StrEnum):
    PENDING = "pending"
    READING = "reading"
    WRITING = "writing"
    SUCCEEDED = "succeeded"
    UNCHANGED = "unchanged"
    CONFLICT = "conflict"
    PERMISSION = "permission"
    MISSING = "missing"
    FAILED = "failed"
    AMBIGUOUS = "ambiguous"


class CollectionKind(StrEnum):
    STATIC = "static"
    #: Reserved. Dynamic (FilterSet-backed) collections are post-MVP; the
    #: column exists so they are a purely additive change.
    DYNAMIC = "dynamic"


# ---------------------------------------------------------------------------
# Settings / runtime
# ---------------------------------------------------------------------------


class AppSettings(Base):
    """Singleton row of user-editable, **non-secret** settings.

    There is deliberately no token column: the Paperless token lives only in
    the environment and is never persisted (ADR-0002).
    """

    __tablename__ = "settings"
    __table_args__ = (CheckConstraint("id = 1", name="singleton"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    default_page_size: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    max_concurrency: Mapped[int] = mapped_column(Integer, default=4, nullable=False)
    theme: Mapped[str] = mapped_column(String(16), default="system", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class UserPreference(Base):
    """Non-secret UI preferences keyed by the stable Paperless user identity."""

    __tablename__ = "user_preferences"
    __table_args__ = (
        CheckConstraint("locale IN ('en', 'fr')", name="locale_supported"),
    )

    owner_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    locale: Mapped[str] = mapped_column(String(8), nullable=False, default="en")
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class RuntimeLock(Base):
    """Single-instance guard.

    The in-process Job Engine is only safe if exactly one scheduler exists.
    Two Uvicorn workers would race over the same job rows and double-write to
    Paperless. See ADR-0006.
    """

    __tablename__ = "runtime_lock"
    __table_args__ = (CheckConstraint("id = 1", name="singleton"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    instance_id: Mapped[str] = mapped_column(String(64), nullable=False)
    acquired_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, nullable=False)
    heartbeat_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, nullable=False)


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------


class Job(Base):
    """A significant bulk mutation, durable across restarts."""

    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_status", "status"),
        Index("ix_jobs_created_at", "created_at"),
        Index("ix_jobs_owner_created", "owner_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    #: Null only for quarantined v0.1 history. Never exposed to a user.
    owner_id: Mapped[int | None] = mapped_column(Integer, index=True)
    type: Mapped[JobType] = mapped_column(String(32), nullable=False)
    status: Mapped[JobStatus] = mapped_column(
        String(32), default=JobStatus.PENDING, nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)

    #: How the dataset was obtained. Kept for History even though execution
    #: uses the snapshot below.
    filterset_json: Mapped[str | None] = mapped_column(Text)
    transformation_json: Mapped[str | None] = mapped_column(Text)

    #: Staging may expire; this identifier intentionally has no preview FK.
    preview_id: Mapped[str | None] = mapped_column(String(32), unique=True, index=True)
    preview_summary_json: Mapped[str | None] = mapped_column(Text)
    source_kind: Mapped[str | None] = mapped_column(String(16))
    acknowledge_external_race: Mapped[bool] = mapped_column(default=False, nullable=False)

    rollback_of_job_id: Mapped[int | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL")
    )

    total_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    succeeded_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    skipped_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    #: Which Paperless contract this job was executed against - relevant when
    #: interpreting old history after a Paperless upgrade.
    paperless_api_version: Mapped[int | None] = mapped_column(Integer)
    paperless_server_version: Mapped[str | None] = mapped_column(String(32))

    error: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    #: Liveness proof; a stale heartbeat on a RUNNING job means "possibly
    #: stalled" rather than silently spinning.
    heartbeat_at: Mapped[datetime | None] = mapped_column(UtcDateTime)

    operations: Mapped[list[JobOperation]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    @property
    def pending_count(self) -> int:
        return max(
            0,
            self.total_count - self.succeeded_count - self.failed_count - self.skipped_count,
        )


class JobOperation(Base):
    """One document + one field: the finest granularity supporting partial
    failure and per-field rollback.

    Three values are recorded, not two (ADR-0005):

    * ``before_value``   - what was there beforehand.
    * ``intended_value`` - what we asked Paperless to write.
    * ``written_value``  - what Paperless actually confirmed.

    Rollback conflict detection compares the *current* value against
    ``written_value``. Comparing against ``intended_value`` would report
    phantom conflicts on correctly-written documents, because Paperless
    normalises input (monetary currency prefixes, select option IDs, date
    coercion, tag hierarchy).
    """

    __tablename__ = "job_operations"
    __table_args__ = (
        Index("ix_ops_job", "job_id", "status"),
        Index("ix_ops_doc", "document_id"),
        # Audit identity only. The durable target state prevents unsafe replay.
        UniqueConstraint(
            "job_id",
            "document_id",
            "field_kind",
            "field_key",
            name="ops_job_doc_field",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )

    #: Paperless document ID. Intentionally no FK - Paperless owns it.
    document_id: Mapped[int] = mapped_column(Integer, nullable=False)
    document_title: Mapped[str | None] = mapped_column(Text)

    field_kind: Mapped[FieldKind] = mapped_column(String(32), nullable=False)
    field_key: Mapped[str] = mapped_column(String(255), nullable=False)

    before_value_json: Mapped[str | None] = mapped_column(Text)
    intended_value_json: Mapped[str | None] = mapped_column(Text)
    written_value_json: Mapped[str | None] = mapped_column(Text)

    #: Full custom-field snapshot taken before the write.
    #:
    #: Safety net for the destructive-PATCH hazard: PATCHing ``custom_fields``
    #: replaces the whole set, so if the read-modify-write rule were ever
    #: violated we can still restore the document. See ADR-0004.
    before_custom_fields_json: Mapped[str | None] = mapped_column(Text)

    #: Document ``modified`` timestamp at preview time, for forward conflict
    #: detection between Preview and Apply.
    source_modified: Mapped[datetime | None] = mapped_column(UtcDateTime)

    status: Mapped[OperationStatus] = mapped_column(
        String(32), default=OperationStatus.PENDING, nullable=False
    )
    error: Mapped[str | None] = mapped_column(Text)
    http_status: Mapped[int | None] = mapped_column(Integer)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    rollback_of_operation_id: Mapped[int | None] = mapped_column(
        ForeignKey("job_operations.id", ondelete="SET NULL")
    )

    started_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime)

    job: Mapped[Job] = relationship(back_populates="operations")


class JobTarget(Base):
    """Immutable target identity plus the durable per-document execution state."""

    __tablename__ = "job_targets"
    __table_args__ = (
        UniqueConstraint("job_id", "position", name="target_job_position"),
        Index("ix_targets_claim", "job_id", "status", "position"),
    )
    job_id: Mapped[int] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True
    )
    document_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    preview_json: Mapped[str | None] = mapped_column(Text)
    execution_owner: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[TargetStatus] = mapped_column(
        String(32), nullable=False, default=TargetStatus.PENDING
    )
    before_custom_fields_json: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    http_status: Mapped[int | None] = mapped_column(Integer)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime)


# ---------------------------------------------------------------------------
# Schemas / Quality
# ---------------------------------------------------------------------------


class DocumentSchema(Base):
    """Expectations for a class of documents.

    ``rules_json`` is a single typed rule list rather than separate
    "required fields" and "expected metadata" concepts, so future rule kinds
    (min, max, regex, date_range, unique, expected_tags) are additive.
    See ADR-0007.
    """

    __tablename__ = "schemas"
    __table_args__ = (
        UniqueConstraint("owner_id", "name", name="schema_owner_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    #: Null marks legacy data that is deliberately not assigned on upgrade.
    owner_id: Mapped[int | None] = mapped_column(Integer, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    applies_when_json: Mapped[str] = mapped_column(Text, nullable=False)
    rules_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


# ---------------------------------------------------------------------------
# Collections
# ---------------------------------------------------------------------------


class Collection(Base):
    """A lightweight, user-curated set of documents."""

    __tablename__ = "collections"
    __table_args__ = (
        UniqueConstraint("owner_id", "name", name="collection_owner_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    #: Null marks legacy data that is deliberately not assigned on upgrade.
    owner_id: Mapped[int | None] = mapped_column(Integer, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[CollectionKind] = mapped_column(
        String(16), default=CollectionKind.STATIC, nullable=False
    )
    #: Unused in MVP; present so dynamic collections need no migration.
    filterset_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    documents: Mapped[list[CollectionDocument]] = relationship(
        back_populates="collection",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class CollectionDocument(Base):
    """Membership of a Paperless document in a static collection."""

    __tablename__ = "collection_documents"

    collection_id: Mapped[int] = mapped_column(
        ForeignKey("collections.id", ondelete="CASCADE"), primary_key=True
    )
    #: Paperless document ID; no FK by design.
    document_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    added_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, nullable=False)

    collection: Mapped[Collection] = relationship(back_populates="documents")


__all__ = [
    "AppSettings",
    "Base",
    "Collection",
    "CollectionDocument",
    "CollectionKind",
    "DocumentSchema",
    "FieldKind",
    "Job",
    "JobOperation",
    "JobStatus",
    "JobType",
    "OperationStatus",
    "RuntimeLock",
    "UserPreference",
]

# Silence "imported but unused" for the re-exported Base.
_ = Any


class Preview(Base):
    """Expiring M7 staging, never a durable Job or a live document cache."""

    __tablename__ = "previews"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    #: Null only for legacy/abandoned staging; authenticated APIs never expose it.
    owner_id: Mapped[int | None] = mapped_column(Integer, index=True)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, index=True)
    ready: Mapped[bool] = mapped_column(default=False, nullable=False)
    confirmed: Mapped[bool] = mapped_column(default=False, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    summary_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")


class PreviewDocument(Base):
    __tablename__ = "preview_documents"
    __table_args__ = (
        UniqueConstraint("preview_id", "document_id", name="preview_document_identity"),
        Index("ix_preview_status_position", "preview_id", "status", "position"),
    )
    preview_id: Mapped[str] = mapped_column(
        ForeignKey("previews.id", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    result_json: Mapped[str] = mapped_column(Text, nullable=False)
