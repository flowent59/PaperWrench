"""Read-only analytics endpoints."""

import csv
import io

from fastapi import APIRouter
from fastapi import Depends
from fastapi import Response

from paperwrench.analytics import CustomFieldReport
from paperwrench.analytics import CustomFieldReportRequest
from paperwrench.analytics import DashboardCache
from paperwrench.analytics import DashboardRange
from paperwrench.analytics import DashboardSnapshot
from paperwrench.analytics import build_custom_field_report
from paperwrench.analytics import build_dashboard
from paperwrench.analytics.model import ReportValue
from paperwrench.api.deps import get_dashboard_cache
from paperwrench.api.deps import get_metadata_registry
from paperwrench.api.deps import get_paperless_client
from paperwrench.paperless import MetadataRegistry
from paperwrench.paperless import PaperlessClient

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/dashboard", response_model=DashboardSnapshot)
async def dashboard(
    range: DashboardRange = DashboardRange.DAYS_30,
    client: PaperlessClient = Depends(get_paperless_client),
    registry: MetadataRegistry = Depends(get_metadata_registry),
    cache: DashboardCache = Depends(get_dashboard_cache),
) -> DashboardSnapshot:
    return await cache.get_or_build(
        range,
        lambda: build_dashboard(
            client,
            registry,
            range,
            cache_ttl_seconds=cache.ttl_seconds,
        ),
    )


@router.post("/reports", response_model=CustomFieldReport)
async def custom_field_report(
    request: CustomFieldReportRequest,
    client: PaperlessClient = Depends(get_paperless_client),
    registry: MetadataRegistry = Depends(get_metadata_registry),
) -> CustomFieldReport:
    return await build_custom_field_report(client, registry, request)


def _csv_cell(value: object) -> str:
    """Prevent spreadsheet formula execution for user-controlled labels."""
    text = str(value)
    return f"'{text}" if text.startswith(("=", "+", "-", "@", "\t", "\r")) else text


@router.post("/reports/export", response_class=Response)
async def export_custom_field_report(
    request: CustomFieldReportRequest,
    client: PaperlessClient = Depends(get_paperless_client),
    registry: MetadataRegistry = Depends(get_metadata_registry),
) -> Response:
    report = await build_custom_field_report(client, registry, request)
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(
        ["field", "data_type", "group", "documents", "aggregation", "value", "currency"]
    )
    for group in report.groups:
        values: list[ReportValue | None] = [*group.values] if group.values else [None]
        for report_value in values:
            writer.writerow(
                [
                    _csv_cell(report.field_name),
                    report.data_type,
                    _csv_cell(group.label or group.key),
                    group.document_count,
                    report.aggregation,
                    report_value.value if report_value is not None else group.document_count,
                    report_value.currency or "" if report_value is not None else "",
                ]
            )
    for missing in report.missing:
        writer.writerow(
            [
                _csv_cell(report.field_name),
                report.data_type,
                f"missing:{missing.kind}",
                missing.count,
                "count",
                missing.count,
                "",
            ]
        )
    filename = f"paperwrench-{report.field_id}-{report.range.value}.csv"
    return Response(
        content="\ufeff" + output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
