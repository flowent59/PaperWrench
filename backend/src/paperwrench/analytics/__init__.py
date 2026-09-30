"""Permission-aware, read-only analytics over Paperless documents."""

from paperwrench.analytics.model import CustomFieldReport
from paperwrench.analytics.model import CustomFieldReportRequest
from paperwrench.analytics.model import DashboardRange
from paperwrench.analytics.model import DashboardSnapshot
from paperwrench.analytics.reports import build_custom_field_report
from paperwrench.analytics.service import DashboardCache
from paperwrench.analytics.service import build_dashboard

__all__ = [
    "CustomFieldReport",
    "CustomFieldReportRequest",
    "DashboardCache",
    "DashboardRange",
    "DashboardSnapshot",
    "build_custom_field_report",
    "build_dashboard",
]
