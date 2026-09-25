"""Bounded, read-only schema violation pages."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi import Depends
from fastapi import Query
from sqlalchemy.orm import Session

from paperwrench.api.deps import get_metadata_registry
from paperwrench.api.deps import get_paperless_client
from paperwrench.api.v1.documents import build_query_params
from paperwrench.api.v1.schemas import _definition
from paperwrench.api.v1.schemas import _row
from paperwrench.db.session import get_db
from paperwrench.filters import DatasetPageRequest
from paperwrench.filters import build_catalog
from paperwrench.paperless import MetadataRegistry
from paperwrench.paperless import PaperlessClient
from paperwrench.quality.model import QualityFinding
from paperwrench.quality.model import QualityPage
from paperwrench.quality.model import QualityRuleSummary
from paperwrench.quality.service import exact_rule_query
from paperwrench.schemas.model import RuleStatus
from paperwrench.schemas.service import evaluate_document
from paperwrench.schemas.service import validate_rules

router = APIRouter(prefix="/quality", tags=["quality"])


@router.get("/schemas/{schema_id}", response_model=QualityPage)
async def quality_page(
    schema_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=25, le=100),
    db: Session = Depends(get_db),
    client: PaperlessClient = Depends(get_paperless_client),
    registry: MetadataRegistry = Depends(get_metadata_registry),
) -> QualityPage:
    schema = _definition(_row(db, schema_id))
    catalog = await build_catalog(registry)
    validate_rules(schema.rules, catalog)
    definitions = {field.id: field for field in await registry.all_custom_fields()}
    request = DatasetPageRequest(
        **schema.applies_when.model_dump(exclude_none=True), page=page, page_size=page_size
    )
    params = await build_query_params(request, registry=registry)
    batch = await client.list_documents(params=params, page=page, page_size=page_size)
    evaluated = [evaluate_document(schema, document, definitions) for document in batch.results]
    findings = [
        QualityFinding(document_id=document.document_id, title=document.title, rule=rule)
        for document in evaluated
        for rule in document.rules
        if rule.status is RuleStatus.FAIL
    ]
    summaries = [
        QualityRuleSummary(
            rule_index=index,
            violation_count=sum(1 for item in findings if item.rule.rule_index == index),
            exact_query=exact_rule_query(schema, rule, catalog),
            page_document_ids=sorted(
                {item.document_id for item in findings if item.rule.rule_index == index}
            ),
        )
        for index, rule in enumerate(schema.rules)
    ]
    return QualityPage(
        schema_id=schema_id,
        schema_name=schema.name,
        items=findings,
        rules=summaries,
        page=page,
        page_size=page_size,
        page_count=(batch.count + page_size - 1) // page_size,
        dataset_total=batch.count,
        evaluated_count=len(evaluated),
        violation_count=len(findings),
    )
