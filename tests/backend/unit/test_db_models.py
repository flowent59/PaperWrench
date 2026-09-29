"""Model-level invariants that later milestones depend on."""

from __future__ import annotations

import pytest
from sqlalchemy import func
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from paperwrench.db.models import Collection
from paperwrench.db.models import CollectionDocument
from paperwrench.db.models import CollectionKind
from paperwrench.db.models import FieldKind
from paperwrench.db.models import Job
from paperwrench.db.models import JobOperation
from paperwrench.db.models import JobStatus
from paperwrench.db.models import JobType
from paperwrench.db.models import OperationStatus
from paperwrench.db.models import UserPreference


def _job(session: Session, **kwargs: object) -> Job:
    kwargs.setdefault("type", JobType.TRANSFORM)
    kwargs.setdefault("title", "Rename documents")
    job = Job(**kwargs)
    session.add(job)
    session.commit()
    return job


def test_job_defaults_to_pending(session: Session) -> None:
    job = _job(session)
    assert job.status == JobStatus.PENDING
    assert job.preview_id is None
    assert job.created_at is not None


def test_operation_stores_three_values(session: Session) -> None:
    """before / intended / written - the basis of sound rollback (ADR-0005)."""
    job = _job(session)
    op = JobOperation(
        job_id=job.id,
        document_id=184,
        field_kind=FieldKind.CORE,
        field_key="title",
        before_value_json='"Vacations f\\u00e9vrier"',
        intended_value_json='"Relev\\u00e9 de vacations \\u2013 F\\u00e9vrier 2024"',
        written_value_json='"Relev\\u00e9 de vacations \\u2013 F\\u00e9vrier 2024"',
        before_custom_fields_json='[{"field": 7, "value": "482.31"}]',
    )
    session.add(op)
    session.commit()

    stored = session.execute(select(JobOperation)).scalar_one()
    assert stored.before_value_json is not None
    assert stored.intended_value_json is not None
    assert stored.written_value_json is not None
    # Full CF snapshot is the safety net for the destructive-PATCH hazard.
    assert stored.before_custom_fields_json is not None
    assert stored.status == OperationStatus.PENDING
    assert stored.attempts == 0


def test_duplicate_operation_for_same_document_field_is_rejected(session: Session) -> None:
    """Idempotency guard: a resumed job must never double-apply an operation."""
    job = _job(session)
    for _ in range(2):
        session.add(
            JobOperation(
                job_id=job.id,
                document_id=184,
                field_kind=FieldKind.CORE,
                field_key="title",
            )
        )
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_same_document_different_field_is_allowed(session: Session) -> None:
    job = _job(session)
    session.add_all(
        [
            JobOperation(
                job_id=job.id, document_id=184, field_kind=FieldKind.CORE, field_key="title"
            ),
            JobOperation(
                job_id=job.id,
                document_id=184,
                field_kind=FieldKind.CUSTOM_FIELD,
                field_key="7",
            ),
        ]
    )
    session.commit()
    assert session.execute(select(func.count()).select_from(JobOperation)).scalar_one() == 2


def test_deleting_job_cascades_to_operations(session: Session) -> None:
    job = _job(session)
    session.add(
        JobOperation(
            job_id=job.id, document_id=1, field_kind=FieldKind.CORE, field_key="title"
        )
    )
    session.commit()

    session.delete(job)
    session.commit()
    assert session.execute(select(func.count()).select_from(JobOperation)).scalar_one() == 0


def test_rollback_is_relational_not_a_status(session: Session) -> None:
    """Partial rollbacks must be representable (ADR-0006)."""
    original = _job(session)
    rollback = _job(session, rollback_of_job_id=original.id, type=JobType.ROLLBACK)
    assert rollback.rollback_of_job_id == original.id
    assert rollback.status in set(JobStatus)


def test_pending_count_is_derived(session: Session) -> None:
    job = _job(session, total_count=124, succeeded_count=108, failed_count=0, skipped_count=0)
    assert job.pending_count == 16


def test_terminal_status_helpers() -> None:
    assert JobStatus.COMPLETED.is_terminal
    assert JobStatus.PARTIAL.is_terminal
    assert not JobStatus.RUNNING.is_terminal
    # INTERRUPTED is resumable, so it is not terminal.
    assert not JobStatus.INTERRUPTED.is_terminal
    assert OperationStatus.SKIPPED_CONFLICT.is_terminal
    assert not OperationStatus.PENDING.is_terminal


def test_collection_defaults_to_static_with_reserved_filterset(session: Session) -> None:
    """Dynamic collections must be additive later, not a migration."""
    collection = Collection(name="SPV à vérifier")
    session.add(collection)
    session.commit()
    assert collection.kind == CollectionKind.STATIC
    assert collection.filterset_json is None


def test_collection_membership_is_unique_and_cascades(session: Session) -> None:
    collection = Collection(name="Factures 2026")
    session.add(collection)
    session.commit()

    session.add(CollectionDocument(collection_id=collection.id, document_id=42))
    session.commit()

    session.add(CollectionDocument(collection_id=collection.id, document_id=42))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    session.delete(collection)
    session.commit()
    remaining = session.execute(select(func.count()).select_from(CollectionDocument))
    assert remaining.scalar_one() == 0


def test_utc_datetime_roundtrips_timezone_aware(session: Session) -> None:
    """SQLite would otherwise return naive datetimes and break comparisons."""
    _job(session)
    session.expire_all()
    reloaded = session.execute(select(Job)).scalar_one()
    assert reloaded.created_at.tzinfo is not None


def test_user_locale_is_per_owner_and_rejects_unsupported_values(session: Session) -> None:
    session.add_all(
        [
            UserPreference(owner_id=1, locale="en"),
            UserPreference(owner_id=2, locale="fr"),
        ]
    )
    session.commit()
    assert session.get(UserPreference, 1).locale == "en"  # type: ignore[union-attr]
    assert session.get(UserPreference, 2).locale == "fr"  # type: ignore[union-attr]

    session.add(UserPreference(owner_id=3, locale="de"))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
