"""Calendar recurrence, with explicit daylight-saving semantics."""

from datetime import UTC
from datetime import datetime
from datetime import time
from datetime import timedelta
from typing import Literal
from zoneinfo import ZoneInfo
from zoneinfo import ZoneInfoNotFoundError

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import field_validator


class Recurrence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timezone: str = Field(min_length=1, max_length=100)
    frequency: Literal["daily", "weekly"] = "daily"
    hour: int = Field(ge=0, le=23)
    minute: int = Field(ge=0, le=59)
    weekday: int = Field(default=0, ge=0, le=6)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("Unknown IANA timezone") from exc
        return value

    def next_after(self, after: datetime) -> datetime:
        """Skip nonexistent times; use the first fold once for repeated times.

        Missed occurrences are never replayed in a catch-up burst.
        """
        zone = ZoneInfo(self.timezone)
        start = after.astimezone(zone).date()
        for offset in range(15):
            day = start + timedelta(days=offset)
            if self.frequency == "weekly" and day.weekday() != self.weekday:
                continue
            local = datetime.combine(day, time(self.hour, self.minute), tzinfo=zone)
            candidate = local.astimezone(UTC)
            if candidate.astimezone(zone).replace(tzinfo=None) != local.replace(tzinfo=None):
                continue
            if candidate > after:
                return candidate
        raise ValueError("No upcoming occurrence")
