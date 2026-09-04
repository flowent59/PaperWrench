"""Single-instance runtime lock.

Two schedulers would double-write to Paperless, so this must fail loudly
rather than degrade. See ADR-0006.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from paperwrench.db.base import utcnow
from paperwrench.db.lock import STALE_AFTER
from paperwrench.db.lock import acquire_lock
from paperwrench.db.lock import build_instance_id
from paperwrench.db.lock import refresh_lock
from paperwrench.db.lock import release_lock
from paperwrench.db.models import RuntimeLock
from paperwrench.errors import SingleInstanceViolationError


def test_first_instance_acquires_lock(session: Session) -> None:
    lock = acquire_lock(session, "instance-a")
    assert lock.instance_id == "instance-a"
    assert lock.id == 1


def test_second_live_instance_is_rejected(session: Session) -> None:
    acquire_lock(session, "instance-a")
    with pytest.raises(SingleInstanceViolationError) as exc:
        acquire_lock(session, "instance-b")
    assert exc.value.details is not None
    assert exc.value.details["holder_instance_id"] == "instance-a"


def test_same_instance_can_reacquire(session: Session) -> None:
    """Dev reload must not deadlock the app against itself."""
    first = acquire_lock(session, "instance-a")
    original = first.acquired_at
    again = acquire_lock(session, "instance-a")
    assert again.instance_id == "instance-a"
    assert again.acquired_at == original


def test_stale_lock_is_taken_over(session: Session) -> None:
    """A crashed process must not block restarts forever."""
    acquire_lock(session, "dead-instance")
    lock = session.execute(select(RuntimeLock)).scalar_one()
    lock.heartbeat_at = utcnow() - STALE_AFTER * 2
    session.commit()

    taken = acquire_lock(session, "new-instance")
    assert taken.instance_id == "new-instance"


def test_force_overrides_a_live_lock(session: Session) -> None:
    acquire_lock(session, "instance-a")
    taken = acquire_lock(session, "instance-b", force=True)
    assert taken.instance_id == "instance-b"


def test_refresh_only_succeeds_for_the_holder(session: Session) -> None:
    acquire_lock(session, "instance-a")
    assert refresh_lock(session, "instance-a") is True
    assert refresh_lock(session, "instance-b") is False


def test_release_is_scoped_to_the_holder(session: Session) -> None:
    acquire_lock(session, "instance-a")
    release_lock(session, "instance-b")
    assert session.execute(select(RuntimeLock)).scalar_one_or_none() is not None

    release_lock(session, "instance-a")
    assert session.execute(select(RuntimeLock)).scalar_one_or_none() is None


def test_instance_ids_are_unique() -> None:
    assert build_instance_id() != build_instance_id()
