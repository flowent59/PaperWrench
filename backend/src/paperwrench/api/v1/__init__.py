"""PaperWrench API v1.

Design rule: the frontend speaks *PaperWrench* vocabulary. Paperless query
syntax and ``custom_field_query`` JSON never leak into frontend code.
"""

from __future__ import annotations

from fastapi import APIRouter

from paperwrench.api.v1 import documents
from paperwrench.api.v1 import filters
from paperwrench.api.v1 import inspector
from paperwrench.api.v1 import metadata
from paperwrench.api.v1 import system

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(system.router)
api_router.include_router(metadata.router)
api_router.include_router(documents.router)
api_router.include_router(filters.router)
api_router.include_router(inspector.router)

__all__ = ["api_router"]
