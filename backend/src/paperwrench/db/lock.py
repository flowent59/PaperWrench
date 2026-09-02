"""Single-instance runtime lock.

PaperWrench's MVP Job Engine runs in-process (no Redis/Celery). That is only
safe if exactly one scheduler exists: two server processes would race over the
same ``jobs`` rows and double-write to Paperless.

This module makes that constraint *enforced* rather than merely documented. A
second live instance fails fast with an explicit error. See ADR-0006.
"""

from __future__ import annotations

import os
import uuid
from datetime import timedelta

from sqlalchemy import select
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
    return f"{os.uname().nodename}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def acquire_lock(session: Session, instance_id: str, *, force: bool = False) -> RuntimeLock:
    """Acquire the single-instance lock.

    Raises:
        SingleInstanceViolationError: if another instance holds a live lock.
    """
    now = utcnow()
    existing = session.execute(select(RuntimeLock).where(RuntimeLock.id == 1)).scalar_one_or_none()

    if existing is None:
        lock = RuntimeLock(id=1, instance_id=instance_id, acquired_at=now, heartbeat_at=now)
        session.add(lock)
        session.commit()
        logger.info("runtime_lock_acquired", instance_id=instance_id, took_over=False)
        return lock

    # Re-acquiring our own lock (e.g. reload in dev) is fine.
    if existing.instance_id == instance_id:
        existing.heartbeat_at = now
        session.commit()
        return existing

    age = now - existing.heartbeat_at
    if age <= STALE_AFTER and not force:
        raise SingleInstanceViolationError(
            "Another PaperWrench instance appears to be running. PaperWrench's MVP job "
            "engine runs in-process and must be single-instance: two schedulers would "
            "double-write to Paperless. Stop the other instance, or set "
            "PAPERWRENCH_FORCE_LOCK=true if you are certain it is dead.",
            details={
                "holder_instance_id": existing.instance_id,
                "heartbeat_age_seconds": int(age.total_seconds()),
            },
        )

    logger.warning(
        "runtime_lock_taken_over",
        previous_instance_id=existing.instance_id,
        heartbeat_age_seconds=int(age.total_seconds()),
        forced=force,
    )
    existing.instance_id = instance_id
    existing.acquired_at = now
    existing.heartbeat_at = now
    session.commit()
    return existing


def refresh_lock(session: Session, instance_id: str) -> bool:
    """Refresh the heartbeat. Returns False if we no longer hold the lock."""
    lock = session.execute(select(RuntimeLock).where(RuntimeLock.id == 1)).scalar_one_or_none()
    if lock is None or lock.instance_id != instance_id:
        return False
    lock.heartbeat_at = utcnow()
    session.commit()
    return True


def release_lock(session: Session, instance_id: str) -> None:
    """Release the lock on clean shutdown (best effort)."""
    lock = session.execute(select(RuntimeLock).where(RuntimeLock.id == 1)).scalar_one_or_none()
    if lock is not None and lock.instance_id == instance_id:
        session.delete(lock)
        session.commit()
        logger.info("runtime_lock_released", instance_id=instance_id)
