"""PaperWrench API v1.

Design rule: the frontend speaks *PaperWrench* vocabulary. Paperless query
syntax and ``custom_field_query`` JSON never leak into frontend code.
"""

from __future__ import annotations

from fastapi import APIRouter

from paperwrench.api.v1 import system

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(system.router)

__all__ = ["api_router"]
