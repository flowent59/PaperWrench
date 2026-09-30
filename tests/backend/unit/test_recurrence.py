from datetime import datetime

import pytest
from pydantic import ValidationError

from paperwrench.schedules.recurrence import Recurrence


def test_daily_dst_gap_and_fold() -> None:
    rule = Recurrence(timezone="Europe/Paris", hour=2, minute=30)
    assert rule.next_after(datetime.fromisoformat("2026-03-28T02:00:00+00:00")) == (
        datetime.fromisoformat("2026-03-30T00:30:00+00:00")
    )
    assert rule.next_after(datetime.fromisoformat("2026-10-24T02:00:00+00:00")) == (
        datetime.fromisoformat("2026-10-25T00:30:00+00:00")
    )
    # Never run again during the second 02:30 on the same local day.
    assert rule.next_after(datetime.fromisoformat("2026-10-25T00:30:00+00:00")) == (
        datetime.fromisoformat("2026-10-26T01:30:00+00:00")
    )


def test_weekly_and_timezone_validation() -> None:
    rule = Recurrence(timezone="Asia/Kolkata", frequency="weekly", weekday=0, hour=9, minute=0)
    assert rule.next_after(datetime.fromisoformat("2026-09-30T00:00:00+00:00")) == (
        datetime.fromisoformat("2026-10-05T03:30:00+00:00")
    )
    with pytest.raises(ValidationError):
        Recurrence(timezone="invalid", hour=1, minute=0)
