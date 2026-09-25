"""Quality page counts are explicitly scoped to evaluated documents."""

from __future__ import annotations

from pydantic import BaseModel

from paperwrench.filters.model import DatasetQuery
from paperwrench.schemas.model import RuleResult


class QualityFinding(BaseModel):
    document_id: int
    title: str
    rule: RuleResult


class QualityRuleSummary(BaseModel):
    rule_index: int
    violation_count: int
    exact_query: DatasetQuery | None
    page_document_ids: list[int]


class QualityPage(BaseModel):
    schema_id: int
    schema_name: str
    items: list[QualityFinding]
    rules: list[QualityRuleSummary]
    page: int
    page_size: int
    page_count: int
    dataset_total: int
    evaluated_count: int
    violation_count: int
