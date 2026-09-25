"""Exact rule drill-downs expressed only with the M4 FilterSet."""

from __future__ import annotations

from paperwrench.filters.catalog import FieldCatalog
from paperwrench.filters.catalog import FieldType
from paperwrench.filters.issues import FilterNotCompilable
from paperwrench.filters.issues import FilterValidationError
from paperwrench.filters.model import CustomFieldRef
from paperwrench.filters.model import DatasetQuery
from paperwrench.filters.model import FilterCondition
from paperwrench.filters.model import FilterGroup
from paperwrench.filters.model import FilterOperator
from paperwrench.filters.model import FilterSet
from paperwrench.filters.model import GroupOperator
from paperwrench.filters.service import validate_and_compile
from paperwrench.schemas.model import RequiredRule
from paperwrench.schemas.model import SchemaDefinition
from paperwrench.schemas.model import SchemaRule


def exact_rule_query(
    schema: SchemaDefinition, rule: SchemaRule, catalog: FieldCatalog
) -> DatasetQuery | None:
    """Return a proven exact complement, or leave the caller with explicit IDs.

    Core fields and equals rules have no general exact complement in M4.
    A document-link field may hold an empty list, which is required-failing
    but is not represented by the M4 missing/null operators.
    """
    if not isinstance(rule, RequiredRule) or not isinstance(rule.field, CustomFieldRef):
        return None
    if rule.field_type is FieldType.DOCUMENT_LINK:
        return None
    empty_operator = (
        FilterOperator.IS_EMPTY
        if rule.field_type in {FieldType.TEXT, FieldType.LONG_TEXT, FieldType.URL}
        else FilterOperator.IS_NULL
    )
    complement = FilterGroup(
        operator=GroupOperator.OR,
        children=[
            FilterCondition(field=rule.field, operator=FilterOperator.IS_MISSING),
            FilterCondition(field=rule.field, operator=empty_operator),
        ],
    )
    scope = schema.applies_when.filters
    children = [complement] if scope is None or scope.is_empty else [scope.root, complement]
    filters = FilterSet(root=FilterGroup(operator=GroupOperator.AND, children=children))
    try:
        validate_and_compile(filters, catalog)
    except (FilterNotCompilable, FilterValidationError):
        return None
    return DatasetQuery(
        search=schema.applies_when.search,
        filters=filters,
        ordering=schema.applies_when.ordering,
    )
