"""Single-instance runtime lock.

PaperWrench's MVP Job Engine runs in-process (no Redis/Celery). That is only
safe if exactly one scheduler exists: two server processes would race over the
same ``jobs`` rows and double-write to Paperless.

This module makes that constraint *enforced* rather than merely documented. A
second live instance fails fast with an explicit error. See ADR-0006.
"""

from __future__ import annotations

import os
import platform
import uuid
from datetime import timedelta

from sqlalchemy import case
from sqlalchemy import delete
from sqlalchemy import literal
from sqlalchemy import or_
from sqlalchemy import select
from sqlalchemy import update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from paperwrench.db.base import utcnow
from paperwrench.db.models import RuntimeLock
from paperwrench.errors import SingleInstanceViolationError
from paperwrench.logging import get_logger

logger = get_logger(__name__)

#: A lock whose heartbeat is older than this is considered abandoned (the
#: previous process died without releasing it).
STALE_AFTER = timedelta(seconds=60)

#: How often the running instance refreshes its heartbeat.
HEARTBEAT_INTERVAL_SECONDS = 15


def build_instance_id() -> str:
    """Identify this process (hostname/PID plus randomness for containers)."""
    return f"{platform.node()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def acquire_lock(session: Session, instance_id: str, *, force: bool = False) -> RuntimeLock:
    """Acquire the single-instance lock.

    Raises:
        SingleInstanceViolationError: if another instance holds a live lock.
    """
    now = utcnow()
    existing = session.execute(select(RuntimeLock).where(RuntimeLock.id == 1)).scalar_one_or_none()

    statement = (
        insert(RuntimeLock)
        .values(id=1, instance_id=instance_id, acquired_at=now, heartbeat_at=now)
        .on_conflict_do_update(
            index_elements=[RuntimeLock.id],
            set_={
                "instance_id": instance_id,
                "heartbeat_at": now,
                "acquired_at": case(
                    (RuntimeLock.instance_id == instance_id, RuntimeLock.acquired_at), else_=now
                ),
            },
            where=or_(
                RuntimeLock.instance_id == instance_id,
                RuntimeLock.heartbeat_at < now - STALE_AFTER,
                literal(force),
            ),
        )
        .returning(RuntimeLock)
    )
    lock = session.scalars(statement, execution_options={"populate_existing": True}).one_or_none()
    if lock is None:
        raise SingleInstanceViolationError(
            "Another PaperWrench instance appears to be running. PaperWrench's MVP job "
            "engine runs in-process and must be single-instance: two schedulers would "
            "double-write to Paperless. Stop the other instance, or set "
            "PAPERWRENCH_FORCE_LOCK=true if you are certain it is dead.",
            details={
                "holder_instance_id": existing.instance_id if existing else "unknown",
            },
        )

    session.commit()
    logger.info("runtime_lock_acquired", instance_id=instance_id, forced=force)
    return lock


def refresh_lock(session: Session, instance_id: str) -> bool:
    """Refresh the heartbeat. Returns False if we no longer hold the lock."""
    owner = session.execute(
        update(RuntimeLock)
        .where(RuntimeLock.id == 1, RuntimeLock.instance_id == instance_id)
        .values(heartbeat_at=utcnow())
        .returning(RuntimeLock.instance_id)
    ).scalar_one_or_none()
    session.commit()
    return owner is not None


def release_lock(session: Session, instance_id: str) -> None:
    """Release the lock on clean shutdown (best effort)."""
    owner = session.execute(
        delete(RuntimeLock)
        .where(RuntimeLock.id == 1, RuntimeLock.instance_id == instance_id)
        .returning(RuntimeLock.instance_id)
    ).scalar_one_or_none()
    session.commit()
    if owner is not None:
        logger.info("runtime_lock_released", instance_id=instance_id)
