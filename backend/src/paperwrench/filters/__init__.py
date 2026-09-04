"""The Filter Engine (M4).

A reusable query primitive, not a feature of the Explorer. The same
``FilterSet`` is meant to be what Transform, Dry Run, Jobs, Quality, Schemas,
Collections, Analytics, Exports and Recipes all mean by "these documents".

The pipeline is always the same, and always in this order::

    FilterSet -> validation -> compiler -> PaperlessQuery

with a hard stop at either step. See :mod:`paperwrench.filters.compiler` for
why there is deliberately no fallback behind that stop.
"""

from __future__ import annotations

from paperwrench.filters.catalog import FieldCatalog
from paperwrench.filters.catalog import FieldType
from paperwrench.filters.catalog import ResolvedField
from paperwrench.filters.catalog import UnknownFieldError
from paperwrench.filters.compiler import PaperlessQuery
from paperwrench.filters.compiler import compile_filterset
from paperwrench.filters.issues import FilterIssue
from paperwrench.filters.issues import FilterIssueCode
from paperwrench.filters.issues import FilterIssueStage
from paperwrench.filters.issues import FilterNotCompilable
from paperwrench.filters.issues import FilterValidationError
from paperwrench.filters.model import CoreField
from paperwrench.filters.model import CoreFieldRef
from paperwrench.filters.model import CustomFieldRef
from paperwrench.filters.model import DatasetPageRequest
from paperwrench.filters.model import DatasetQuery
from paperwrench.filters.model import FilterCondition
from paperwrench.filters.model import FilterGroup
from paperwrench.filters.model import FilterNot
from paperwrench.filters.model import FilterOperator
from paperwrench.filters.model import FilterSet
from paperwrench.filters.model import GroupOperator
from paperwrench.filters.model import SearchMode
from paperwrench.filters.model import SearchSpec
from paperwrench.filters.service import build_catalog
from paperwrench.filters.service import validate_and_compile
from paperwrench.filters.validation import validate_filterset

__all__ = [
    "CoreField",
    "CoreFieldRef",
    "CustomFieldRef",
    "DatasetPageRequest",
    "DatasetQuery",
    "FieldCatalog",
    "FieldType",
    "FilterCondition",
    "FilterGroup",
    "FilterIssue",
    "FilterIssueCode",
    "FilterIssueStage",
    "FilterNot",
    "FilterNotCompilable",
    "FilterOperator",
    "FilterSet",
    "FilterValidationError",
    "GroupOperator",
    "PaperlessQuery",
    "ResolvedField",
    "SearchMode",
    "SearchSpec",
    "UnknownFieldError",
    "build_catalog",
    "compile_filterset",
    "validate_and_compile",
    "validate_filterset",
]
