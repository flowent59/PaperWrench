"""PaperWrench API v1.

Design rule: the frontend speaks *PaperWrench* vocabulary. Paperless query
syntax and ``custom_field_query`` JSON never leak into frontend code.
"""

from __future__ import annotations

from fastapi import APIRouter

from paperwrench.api.v1 import collections
from paperwrench.api.v1 import documents
from paperwrench.api.v1 import filters
from paperwrench.api.v1 import inspector
from paperwrench.api.v1 import jobs
from paperwrench.api.v1 import metadata
from paperwrench.api.v1 import previews
from paperwrench.api.v1 import quality
from paperwrench.api.v1 import schemas
from paperwrench.api.v1 import system
from paperwrench.api.v1 import transformations

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(system.router)
api_router.include_router(metadata.router)
api_router.include_router(documents.router)
api_router.include_router(collections.router)
api_router.include_router(filters.router)
api_router.include_router(inspector.router)
api_router.include_router(transformations.router)
api_router.include_router(previews.router)
api_router.include_router(jobs.router)
api_router.include_router(schemas.router)
api_router.include_router(quality.router)

__all__ = ["api_router"]
