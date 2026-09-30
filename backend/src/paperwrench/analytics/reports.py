"""Typed custom-field reports over an exact, permission-filtered dataset."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC
from datetime import date
from datetime import datetime
from datetime import timedelta
from decimal import Decimal
from decimal import InvalidOperation
from typing import Literal

from paperwrench.analytics.model import CustomFieldReport
from paperwrench.analytics.model import CustomFieldReportRequest
from paperwrench.analytics.model import DashboardRange
from paperwrench.analytics.model import MissingValueBucket
from paperwrench.analytics.model import NumericSemantics
from paperwrench.analytics.model import ReportGroup
from paperwrench.analytics.model import ReportGroupBy
from paperwrench.analytics.model import ReportValue
from paperwrench.errors import ErrorCode
from paperwrench.errors import PaperWrenchError
from paperwrench.filters import CoreField
from paperwrench.filters import CoreFieldRef
from paperwrench.filters import CustomFieldRef
from paperwrench.filters import DatasetQuery
from paperwrench.filters import FilterCondition
from paperwrench.filters import FilterGroup
from paperwrench.filters import FilterNot
from paperwrench.filters import FilterOperator
from paperwrench.filters import FilterSet
from paperwrench.filters import GroupOperator
from paperwrench.filters import build_catalog
from paperwrench.filters import validate_and_compile
from paperwrench.paperless import MetadataRegistry
from paperwrench.paperless import PaperlessClient
from paperwrench.paperless.models import CustomField
from paperwrench.paperless.models import CustomFieldDataType
from paperwrench.paperless.models import Document
from paperwrench.paperless.models import MonetaryAmount

REPORT_PAGE_SIZE = 250
REPORTABLE_TYPES = frozenset(
    {
        CustomFieldDataType.INTEGER,
        CustomFieldDataType.FLOAT,
        CustomFieldDataType.MONETARY,
        CustomFieldDataType.DATE,
    }
)
_DAYS = {
    DashboardRange.DAYS_30: 30,
    DashboardRange.DAYS_90: 90,
    DashboardRange.DAYS_365: 365,
}


@dataclass
class _GroupAccumulator:
    label: str | None
    conditions: list[FilterCondition]
    documents: list[tuple[Document, tuple[str | None, Decimal] | None]] = dataclass_field(
        default_factory=list
    )
    sums: dict[str | None, Decimal] = dataclass_field(
        default_factory=lambda: defaultdict(Decimal)
    )


def _condition(
    field: CoreFieldRef | CustomFieldRef,
    operator: FilterOperator,
    value: object = None,
) -> FilterCondition:
    return FilterCondition(field=field, operator=operator, value=value)


def _combined_filters(
    request: CustomFieldReportRequest, start: date, end: date
) -> FilterSet:
    children: list[FilterCondition | FilterGroup | FilterNot] = [
        _condition(
            CoreFieldRef(name=CoreField.ADDED),
            FilterOperator.GREATER_OR_EQUAL,
            start.isoformat(),
        ),
        _condition(
            CoreFieldRef(name=CoreField.ADDED),
            FilterOperator.LESS_THAN,
            end.isoformat(),
        ),
    ]
    if request.filters is not None:
        children.extend(request.filters.root.children)
    return FilterSet(root=FilterGroup(operator=GroupOperator.AND, children=children))


def _request_filters(request: CustomFieldReportRequest) -> FilterSet:
    children = [] if request.filters is None else list(request.filters.root.children)
    return FilterSet(root=FilterGroup(operator=GroupOperator.AND, children=children))


def _query(base: FilterSet, *extra: FilterCondition) -> DatasetQuery:
    return DatasetQuery(
        filters=FilterSet(
            root=FilterGroup(
                operator=GroupOperator.AND,
                children=[*base.root.children, *extra],
            )
        )
    )


def _added(document: Document) -> datetime | None:
    if document.added is None:
        return None
    try:
        return datetime.fromisoformat(document.added.replace("Z", "+00:00"))
    except ValueError:
        return None


def _date_value(raw: object) -> date | None:
    if not isinstance(raw, str):
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _numeric_value(field: CustomField, raw: object) -> tuple[str | None, Decimal] | None:
    try:
        if field.data_type is CustomFieldDataType.MONETARY:
            if not isinstance(raw, str):
                return None
            monetary = MonetaryAmount.parse(raw)
            return monetary.currency, monetary.amount
        if isinstance(raw, bool) or raw is None:
            return None
        value = Decimal(str(raw))
        return (None, value) if value.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def _group_identity(
    document: Document,
    raw: object,
    field: CustomField,
    request: CustomFieldReportRequest,
    document_types: dict[int, str],
    correspondents: dict[int, str],
    report_start: date,
    report_end: date,
) -> tuple[str, str | None, list[FilterCondition]]:
    custom_ref = CustomFieldRef(field_id=field.id, display_name=field.name)
    if request.group_by in {ReportGroupBy.MONTH, ReportGroupBy.YEAR}:
        value_date = _date_value(raw) if field.data_type is CustomFieldDataType.DATE else None
        added = _added(document)
        source_date = value_date or (added.date() if added is not None else None)
        if source_date is None:
            raise ValueError("document has no usable grouping date")
        if request.group_by is ReportGroupBy.MONTH:
            start = source_date.replace(day=1)
            end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
            key = start.strftime("%Y-%m")
        else:
            start = source_date.replace(month=1, day=1)
            end = start.replace(year=start.year + 1)
            key = str(start.year)
        group_field = (
            custom_ref
            if field.data_type is CustomFieldDataType.DATE
            else CoreFieldRef(name=CoreField.ADDED)
        )
        if field.data_type is not CustomFieldDataType.DATE:
            start = max(start, report_start)
            end = min(end, report_end)
        return key, key, [
            FilterCondition(
                field=group_field,
                operator=FilterOperator.GREATER_OR_EQUAL,
                value=start.isoformat(),
            ),
            FilterCondition(
                field=group_field,
                operator=FilterOperator.LESS_THAN,
                value=end.isoformat(),
            ),
        ]

    if request.group_by is ReportGroupBy.DOCUMENT_TYPE:
        object_id = document.document_type
        names = document_types
        field_ref = CoreFieldRef(name=CoreField.DOCUMENT_TYPE)
    else:
        object_id = document.correspondent
        names = correspondents
        field_ref = CoreFieldRef(name=CoreField.CORRESPONDENT)
    if object_id is None:
        return "missing", None, [_condition(field_ref, FilterOperator.IS_MISSING)]
    return str(object_id), names.get(object_id), [
        _condition(field_ref, FilterOperator.EQUALS, object_id)
    ]


async def build_custom_field_report(
    client: PaperlessClient,
    registry: MetadataRegistry,
    request: CustomFieldReportRequest,
    *,
    today: date | None = None,
) -> CustomFieldReport:
    field = await registry.custom_field_by_id(request.field_id)
    if field.data_type not in REPORTABLE_TYPES:
        raise PaperWrenchError(
            "Only numeric, monetary and date custom fields can be reported.",
            status_code=422,
            code=ErrorCode.VALIDATION_ERROR,
            params={"field_id": field.id, "data_type": field.data_type.value},
        )

    current = today or datetime.now(UTC).date()
    start = current - timedelta(days=_DAYS[request.range] - 1)
    end = current + timedelta(days=1)
    base = _combined_filters(request, start, end)
    compiled = validate_and_compile(base, await build_catalog(registry))
    documents = [
        document
        async for document in client.iter_documents(
            params=compiled.params, page_size=REPORT_PAGE_SIZE
        )
    ]
    document_types = {item.id: item.name for item in await registry.all_document_types()}
    correspondents = {item.id: item.name for item in await registry.all_correspondents()}

    absent = null = invalid = 0
    grouped: dict[str, _GroupAccumulator] = {}
    custom_ref = CustomFieldRef(field_id=field.id, display_name=field.name)
    for document in documents:
        entries = {entry.field: entry for entry in document.custom_fields}
        entry = entries.get(field.id)
        if entry is None:
            absent += 1
            continue
        if entry.value is None:
            null += 1
            continue
        raw = entry.value
        numeric = (
            None
            if field.data_type is CustomFieldDataType.DATE
            else _numeric_value(field, raw)
        )
        if (field.data_type is CustomFieldDataType.DATE and _date_value(raw) is None) or (
            field.data_type is not CustomFieldDataType.DATE and numeric is None
        ):
            invalid += 1
            continue
        try:
            identity = _group_identity(
                document,
                raw,
                field,
                request,
                document_types,
                correspondents,
                start,
                end,
            )
        except ValueError:
            invalid += 1
            continue
        key, label, conditions = identity
        group = grouped.setdefault(
            key,
            _GroupAccumulator(label=label, conditions=conditions),
        )
        group.documents.append((document, numeric))
        if numeric is not None:
            currency, amount = numeric
            group.sums[currency] += amount

    aggregation: Literal["sum", "latest_snapshot", "count"] = "count"
    additive = True
    if field.data_type is not CustomFieldDataType.DATE:
        aggregation = (
            "sum"
            if request.numeric_semantics is NumericSemantics.SUM
            else "latest_snapshot"
        )
        additive = request.numeric_semantics is NumericSemantics.SUM

    groups: list[ReportGroup] = []
    time_grouped_numeric = (
        field.data_type is not CustomFieldDataType.DATE
        and request.group_by in {ReportGroupBy.MONTH, ReportGroupBy.YEAR}
    )
    drill_base = _request_filters(request) if time_grouped_numeric else base
    for key, group in sorted(grouped.items()):
        rows = group.documents
        values: list[ReportValue] = []
        if field.data_type is not CustomFieldDataType.DATE:
            if request.numeric_semantics is NumericSemantics.SUM:
                values = [
                    ReportValue(currency=currency, value=str(value))
                    for currency, value in sorted(
                        group.sums.items(), key=lambda item: item[0] or ""
                    )
                ]
            else:
                latest = max(
                    rows,
                    key=lambda item: _added(item[0])
                    or datetime.min.replace(tzinfo=UTC),
                )
                latest_numeric = latest[1]
                assert latest_numeric is not None
                currency, amount = latest_numeric
                values = [ReportValue(currency=currency, value=str(amount))]
        groups.append(
            ReportGroup(
                key=key,
                label=group.label,
                document_count=len(rows),
                values=values,
                query=_query(
                    drill_base,
                    _condition(custom_ref, FilterOperator.HAS_VALUE),
                    *group.conditions,
                ),
            )
        )

    return CustomFieldReport(
        field_id=field.id,
        field_name=field.name,
        data_type=field.data_type.value,
        range=request.range,
        start=start.isoformat(),
        end=end.isoformat(),
        group_by=request.group_by,
        aggregation=aggregation,
        additive=additive,
        matched_documents=len(documents),
        valued_documents=sum(group.document_count for group in groups),
        groups=groups,
        missing=[
            MissingValueBucket(
                kind="absent",
                count=absent,
                query=_query(base, _condition(custom_ref, FilterOperator.IS_MISSING)),
            ),
            MissingValueBucket(
                kind="null",
                count=null,
                query=_query(base, _condition(custom_ref, FilterOperator.IS_NULL)),
            ),
            MissingValueBucket(kind="invalid", count=invalid),
        ],
    )
