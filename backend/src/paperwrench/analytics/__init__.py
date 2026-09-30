"""Permission-aware, read-only analytics over Paperless documents."""

from paperwrench.analytics.model import DashboardRange
from paperwrench.analytics.model import DashboardSnapshot
from paperwrench.analytics.service import DashboardCache
from paperwrench.analytics.service import build_dashboard

__all__ = ["DashboardCache", "DashboardRange", "DashboardSnapshot", "build_dashboard"]
