"""Wire models for the document dashboard."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel
from pydantic import Field

from paperwrench.filters import DatasetQuery
from paperwrench.filters import FilterSet


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


class ReportGroupBy(StrEnum):
    MONTH = "month"
    YEAR = "year"
    DOCUMENT_TYPE = "document_type"
    CORRESPONDENT = "correspondent"


class NumericSemantics(StrEnum):
    SUM = "sum"
    SNAPSHOT = "snapshot"


class CustomFieldReportRequest(BaseModel):
    field_id: int
    range: DashboardRange = DashboardRange.DAYS_365
    group_by: ReportGroupBy = ReportGroupBy.MONTH
    numeric_semantics: NumericSemantics = NumericSemantics.SUM
    filters: FilterSet | None = None


class ReportValue(BaseModel):
    value: str
    currency: str | None = None


class ReportGroup(BaseModel):
    key: str
    label: str | None
    document_count: int
    values: list[ReportValue] = Field(default_factory=list)
    query: DatasetQuery


class MissingValueBucket(BaseModel):
    kind: Literal["absent", "null", "invalid"]
    count: int
    query: DatasetQuery | None = None


class CustomFieldReport(BaseModel):
    field_id: int
    field_name: str
    data_type: str
    range: DashboardRange
    start: str
    end: str
    group_by: ReportGroupBy
    aggregation: Literal["sum", "latest_snapshot", "count"]
    additive: bool
    matched_documents: int
    valued_documents: int
    groups: list[ReportGroup]
    missing: list[MissingValueBucket]
