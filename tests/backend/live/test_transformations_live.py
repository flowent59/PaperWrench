"""M6 representation check on the disposable 3.1.2 Golden Dataset."""

from __future__ import annotations

import pytest

from paperwrench.paperless import PaperlessClient
from paperwrench.paperless.models import CustomField
from paperwrench.transformations import Transformation
from paperwrench.transformations import evaluate

pytestmark = pytest.mark.live


async def test_golden_period_template_is_read_only(live_client: PaperlessClient) -> None:
    definitions = {field.id: field for field in await live_client.list_custom_fields()}
    period = next(field for field in definitions.values() if field.name == "Période concernée")
    assert isinstance(period, CustomField)
    page = await live_client.list_documents(page_size=100)
    assert page.count >= 17
    with_period = next(
        document for document in page.results if period.id in document.custom_field_map
    )
    without_period = next(
        document for document in page.results if period.id not in document.custom_field_map
    )
    template = Transformation.model_validate({
        "targets": {"source": "ids", "document_ids": [with_period.id, without_period.id]},
        "operations": [{
            "operation": "template",
            "field": {"source": "core", "name": "title"},
            "template": "Relevé de vacations \u2013 {Période concernée}",
            "bindings": {"Période concernée": {
                "source": "custom_field", "field_id": period.id,
            }},
        }],
    })
    present = evaluate(with_period, template, definitions).changes[0]
    absent = evaluate(without_period, template, definitions).changes[0]
    assert present.status in {"change", "unchanged"}
    assert present.intended is not None
    assert present.intended.raw == (
        f"Relevé de vacations \u2013 {with_period.custom_field_map[period.id]}"
    )
    assert absent.status == "error"
    assert absent.issue is not None and absent.issue.code == "TEMPLATE_UNRESOLVED"
    assert await live_client.get_document(with_period.id) == with_period
    assert await live_client.get_document(without_period.id) == without_period
