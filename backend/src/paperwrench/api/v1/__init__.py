"""PaperWrench API v1.

Design rule: the frontend speaks *PaperWrench* vocabulary. Paperless query
syntax and ``custom_field_query`` JSON never leak into frontend code.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi import Depends

from paperwrench.api.deps import require_session
from paperwrench.api.v1 import analytics
from paperwrench.api.v1 import auth
from paperwrench.api.v1 import collections
from paperwrench.api.v1 import documents
from paperwrench.api.v1 import filters
from paperwrench.api.v1 import inspector
from paperwrench.api.v1 import jobs
from paperwrench.api.v1 import metadata
from paperwrench.api.v1 import previews
from paperwrench.api.v1 import quality
from paperwrench.api.v1 import rules
from paperwrench.api.v1 import schedules
from paperwrench.api.v1 import schemas
from paperwrench.api.v1 import system
from paperwrench.api.v1 import transformations
from paperwrench.api.v1 import views

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(system.router)
protected = APIRouter(dependencies=[Depends(require_session)])
protected.include_router(analytics.router)
protected.include_router(metadata.router)
protected.include_router(documents.router)
protected.include_router(collections.router)
protected.include_router(filters.router)
protected.include_router(inspector.router)
protected.include_router(transformations.router)
protected.include_router(previews.router)
protected.include_router(jobs.router)
protected.include_router(schemas.router)
protected.include_router(quality.router)
protected.include_router(views.router)
protected.include_router(rules.router)
protected.include_router(schedules.router)
api_router.include_router(protected)

__all__ = ["api_router"]
