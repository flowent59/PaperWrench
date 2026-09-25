"""Versioned, typed schema rules and the result contract shared with M11."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated
from typing import Any
from typing import Literal
from typing import Union

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from paperwrench.filters.catalog import FieldType
from paperwrench.filters.model import DatasetQuery
from paperwrench.filters.model import FieldRef
from paperwrench.paperless.models import CustomFieldValueKind


class RequiredRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["required"] = "required"
    field: FieldRef
    field_type: FieldType


class EqualsRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["equals"] = "equals"
    field: FieldRef
    field_type: FieldType
    value: Any


SchemaRule = Annotated[
    Union[RequiredRule, EqualsRule],  # noqa: UP007 - pydantic discriminated union
    Field(discriminator="kind"),
]


class SchemaDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    applies_when: DatasetQuery
    rules: list[SchemaRule] = Field(min_length=1, max_length=100)


class StoredScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    query: DatasetQuery


class StoredRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    items: list[SchemaRule] = Field(min_length=1, max_length=100)


class SchemaView(SchemaDefinition):
    id: int
    created_at: str
    updated_at: str


class RuleStatus(StrEnum):
    PASS = "pass"  # noqa: S105 - conformance status, not a credential
    FAIL = "fail"


class RuleResult(BaseModel):
    """Stable per-rule result for document-level consumers, including M11."""

    rule_index: int
    field: FieldRef
    kind: Literal["required", "equals"]
    status: RuleStatus
    value_kind: CustomFieldValueKind
    actual: Any = None
    expected: Any = None
    code: str | None = None


class DocumentSchemaResult(BaseModel):
    document_id: int
    title: str
    status: RuleStatus
    rules: list[RuleResult]


class SchemaEvaluationPage(BaseModel):
    schema_id: int
    items: list[DocumentSchemaResult]
    page: int
    page_size: int
    total: int
    page_count: int
