"""Wire models for the document dashboard."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel
from pydantic import Field

from paperwrench.filters import DatasetQuery


class DashboardRange(StrEnum):
    DAYS_30 = "30d"
    DAYS_90 = "90d"
    DAYS_365 = "365d"


class DashboardPeriod(BaseModel):
    start: str
    end: str
    count: int
    query: DatasetQuery


class DashboardBreakdownItem(BaseModel):
    id: int | None
    label: str | None
    count: int
    query: DatasetQuery


class DashboardBreakdown(BaseModel):
    dimension: str
    items: list[DashboardBreakdownItem] = Field(default_factory=list)


class CustomFieldCoverage(BaseModel):
    field_id: int
    label: str
    data_type: str
    present: int
    missing: int
    present_query: DatasetQuery
    missing_query: DatasetQuery


class DashboardSnapshot(BaseModel):
    range: DashboardRange
    start: str
    end: str
    generated_at: datetime
    cache_ttl_seconds: int
    total_visible: int
    documents_in_range: int
    trend: list[DashboardPeriod]
    breakdowns: list[DashboardBreakdown]
    custom_fields: list[CustomFieldCoverage]
