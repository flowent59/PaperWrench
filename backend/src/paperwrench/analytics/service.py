"""Build bounded dashboard snapshots from the authenticated Paperless view.

Paperless remains the source of truth. Every request is made with the current
session's client, so upstream object permissions define the dataset. The only
local state is a short-lived, per-session snapshot cache.
"""

from __future__ import annotations

import asyncio
import time
from collections import Counter
from collections.abc import Awaitable
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC
from datetime import date
from datetime import datetime
from datetime import timedelta

from paperwrench.analytics.model import CustomFieldCoverage
from paperwrench.analytics.model import DashboardBreakdown
from paperwrench.analytics.model import DashboardBreakdownItem
from paperwrench.analytics.model import DashboardPeriod
from paperwrench.analytics.model import DashboardRange
from paperwrench.analytics.model import DashboardSnapshot
from paperwrench.filters import CoreField
from paperwrench.filters import CoreFieldRef
from paperwrench.filters import CustomFieldRef
from paperwrench.filters import DatasetQuery
from paperwrench.filters import FilterCondition
from paperwrench.filters import FilterGroup
from paperwrench.filters import FilterOperator
from paperwrench.filters import FilterSet
from paperwrench.filters import GroupOperator
from paperwrench.paperless import MetadataRegistry
from paperwrench.paperless import PaperlessClient
from paperwrench.paperless.models import Document

DASHBOARD_PAGE_SIZE = 250
DASHBOARD_CACHE_TTL_SECONDS = 60

_DAYS = {
    DashboardRange.DAYS_30: 30,
    DashboardRange.DAYS_90: 90,
    DashboardRange.DAYS_365: 365,
}


@dataclass
class _CachedSnapshot:
    value: DashboardSnapshot
    stored_at: float


class DashboardCache:
    """Small cache owned by exactly one authenticated session."""

    def __init__(self, *, ttl_seconds: int = DASHBOARD_CACHE_TTL_SECONDS) -> None:
        self.ttl_seconds = ttl_seconds
        self._entries: dict[DashboardRange, _CachedSnapshot] = {}
        self._lock = asyncio.Lock()

    async def get_or_build(
        self,
        selected_range: DashboardRange,
        builder: Callable[[], Awaitable[DashboardSnapshot]],
    ) -> DashboardSnapshot:
        now = time.monotonic()
        cached = self._entries.get(selected_range)
        if cached is not None and now - cached.stored_at <= self.ttl_seconds:
            return cached.value
        async with self._lock:
            now = time.monotonic()
            cached = self._entries.get(selected_range)
            if cached is not None and now - cached.stored_at <= self.ttl_seconds:
                return cached.value
            value: DashboardSnapshot = await builder()
            self._entries[selected_range] = _CachedSnapshot(value=value, stored_at=now)
            return value


def _condition(
    field: CoreFieldRef | CustomFieldRef,
    operator: FilterOperator,
    value: object | None = None,
) -> FilterCondition:
    return FilterCondition(field=field, operator=operator, value=value)


def _query(*conditions: FilterCondition) -> DatasetQuery:
    return DatasetQuery(
        filters=FilterSet(
            root=FilterGroup(operator=GroupOperator.AND, children=list(conditions))
        )
    )


def _range_conditions(start: date, end: date) -> tuple[FilterCondition, FilterCondition]:
    field = CoreFieldRef(name=CoreField.ADDED)
    return (
        _condition(field, FilterOperator.GREATER_OR_EQUAL, start),
        _condition(field, FilterOperator.LESS_THAN, end),
    )


def _parse_added(document: Document) -> datetime | None:
    if document.added is None:
        return None
    try:
        return datetime.fromisoformat(document.added.replace("Z", "+00:00"))
    except ValueError:
        return None


def _bucket_start(value: date, selected_range: DashboardRange) -> date:
    if selected_range is DashboardRange.DAYS_30:
        return value
    if selected_range is DashboardRange.DAYS_90:
        return value - timedelta(days=value.weekday())
    return value.replace(day=1)


def _next_bucket(value: date, selected_range: DashboardRange) -> date:
    if selected_range is DashboardRange.DAYS_30:
        return value + timedelta(days=1)
    if selected_range is DashboardRange.DAYS_90:
        return value + timedelta(days=7)
    return (value.replace(day=28) + timedelta(days=4)).replace(day=1)


def _trend(
    documents: list[Document], selected_range: DashboardRange, start: date, end: date
) -> list[DashboardPeriod]:
    counts: Counter[date] = Counter()
    for document in documents:
        added = _parse_added(document)
        if added is not None:
            counts[_bucket_start(added.date(), selected_range)] += 1

    first = _bucket_start(start, selected_range)
    periods: list[DashboardPeriod] = []
    cursor = first
    while cursor < end:
        bucket_end = min(_next_bucket(cursor, selected_range), end)
        bucket_start = max(cursor, start)
        periods.append(
            DashboardPeriod(
                start=bucket_start.isoformat(),
                end=bucket_end.isoformat(),
                count=counts[cursor],
                query=_query(*_range_conditions(bucket_start, bucket_end)),
            )
        )
        cursor = _next_bucket(cursor, selected_range)
    return periods


def _breakdown(
    *,
    dimension: str,
    counts: Counter[int | None],
    names: dict[int, str],
    base: tuple[FilterCondition, FilterCondition],
) -> DashboardBreakdown:
    core_field = {
        "correspondent": CoreField.CORRESPONDENT,
        "document_type": CoreField.DOCUMENT_TYPE,
        "tags": CoreField.TAGS,
    }[dimension]
    items: list[DashboardBreakdownItem] = []
    for object_id, count in sorted(
        counts.items(), key=lambda item: (-item[1], names.get(item[0] or -1, ""), item[0] or -1)
    ):
        field = CoreFieldRef(name=core_field)
        if object_id is None:
            condition = _condition(field, FilterOperator.IS_MISSING)
        elif dimension == "tags":
            condition = _condition(field, FilterOperator.HAS_ALL_OF, [object_id])
        else:
            condition = _condition(field, FilterOperator.EQUALS, object_id)
        items.append(
            DashboardBreakdownItem(
                id=object_id,
                label=names.get(object_id) if object_id is not None else None,
                count=count,
                query=_query(*base, condition),
            )
        )
    return DashboardBreakdown(dimension=dimension, items=items)


async def build_dashboard(
    client: PaperlessClient,
    registry: MetadataRegistry,
    selected_range: DashboardRange,
    *,
    today: date | None = None,
    cache_ttl_seconds: int = DASHBOARD_CACHE_TTL_SECONDS,
) -> DashboardSnapshot:
    """Aggregate one explicitly bounded window of the user's visible documents."""
    current = today or datetime.now(UTC).date()
    start = current - timedelta(days=_DAYS[selected_range] - 1)
    end = current + timedelta(days=1)
    base = _range_conditions(start, end)
    params = {"added__date__gte": start.isoformat(), "added__date__lt": end.isoformat()}

    total_visible = await client.count_documents()
    documents = [
        document
        async for document in client.iter_documents(params=params, page_size=DASHBOARD_PAGE_SIZE)
    ]
    tags, correspondents, document_types, custom_fields = await asyncio.gather(
        registry.all_tags(),
        registry.all_correspondents(),
        registry.all_document_types(),
        registry.all_custom_fields(),
    )

    correspondent_counts: Counter[int | None] = Counter(
        document.correspondent for document in documents
    )
    document_type_counts: Counter[int | None] = Counter(
        document.document_type for document in documents
    )
    tag_counts: Counter[int | None] = Counter()
    for document in documents:
        if document.tags:
            tag_counts.update(document.tags)
        else:
            tag_counts[None] += 1

    coverage: list[CustomFieldCoverage] = []
    for field in sorted(custom_fields, key=lambda item: item.name.casefold()):
        present = sum(field.id in document.custom_field_map for document in documents)
        ref = CustomFieldRef(field_id=field.id, display_name=field.name)
        coverage.append(
            CustomFieldCoverage(
                field_id=field.id,
                label=field.name,
                data_type=field.data_type.value,
                present=present,
                missing=len(documents) - present,
                present_query=_query(*base, _condition(ref, FilterOperator.IS_PRESENT)),
                missing_query=_query(*base, _condition(ref, FilterOperator.IS_MISSING)),
            )
        )

    return DashboardSnapshot(
        range=selected_range,
        start=start.isoformat(),
        end=end.isoformat(),
        generated_at=datetime.now(UTC),
        cache_ttl_seconds=cache_ttl_seconds,
        total_visible=total_visible,
        documents_in_range=len(documents),
        trend=_trend(documents, selected_range, start, end),
        breakdowns=[
            _breakdown(
                dimension="correspondent",
                counts=correspondent_counts,
                names={item.id: item.name for item in correspondents},
                base=base,
            ),
            _breakdown(
                dimension="document_type",
                counts=document_type_counts,
                names={item.id: item.name for item in document_types},
                base=base,
            ),
            _breakdown(
                dimension="tags",
                counts=tag_counts,
                names={item.id: item.name for item in tags},
                base=base,
            ),
        ],
        custom_fields=coverage,
    )
