"""Closed transformation language; no Paperless transport or execution state."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated
from typing import Any
from typing import Literal
from typing import Union

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import model_validator

from paperwrench.filters.model import DatasetQuery
from paperwrench.filters.model import FieldRef
from paperwrench.paperless.models import CustomFieldValueKind
from paperwrench.paperless.models import TypedCustomFieldValue


class ExplicitTargets(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    source: Literal["ids"] = "ids"
    document_ids: list[int] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_ids(self) -> ExplicitTargets:
        if any(type(item) is not int or item <= 0 for item in self.document_ids):
            raise ValueError("Document IDs must be positive integers")
        if len(set(self.document_ids)) != len(self.document_ids):
            raise ValueError("Document IDs must be unique")
        return self


class DatasetTargets(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Literal["dataset"] = "dataset"
    query: DatasetQuery


Targets = Annotated[Union[ExplicitTargets, DatasetTargets], Field(discriminator="source")]  # noqa: UP007


class SetOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    operation: Literal["set"] = "set"
    field: FieldRef
    value: Any


class ClearOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    operation: Literal["clear"] = "clear"
    field: FieldRef
    # Custom fields require an explicit state. Core fields use their one
    # supported clear value (None for optional scalars, [] for tags).
    state: Literal["absent", "null"] | None = None


class ReplaceOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    operation: Literal["replace"] = "replace"
    field: FieldRef
    find: str = Field(min_length=1)
    replacement: str


class TemplateOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    operation: Literal["template"] = "template"
    field: FieldRef
    template: str
    bindings: dict[str, FieldRef]


Operation = Annotated[
    Union[SetOperation, ClearOperation, ReplaceOperation, TemplateOperation],  # noqa: UP007
    Field(discriminator="operation"),
]


class Transformation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    targets: Targets
    operations: list[Operation] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_fields(self) -> Transformation:
        keys = [operation.field.key for operation in self.operations]
        if len(set(keys)) != len(keys):
            raise ValueError("Each target field may appear only once")
        return self


class ResultStatus(StrEnum):
    CHANGE = "change"
    UNCHANGED = "unchanged"
    ERROR = "error"


class TransformationIssue(BaseModel):
    code: str
    message: str
    field_key: str | None = None


class CoreValue(BaseModel):
    """A core value; custom fields use the existing TypedCustomFieldValue."""

    kind: CustomFieldValueKind
    raw: Any = None


FieldValue = TypedCustomFieldValue | CoreValue


class ProposedChange(BaseModel):
    field: FieldRef
    operation: str
    status: ResultStatus
    before: FieldValue
    intended: FieldValue | None = None
    issue: TransformationIssue | None = None


class EvaluationResult(BaseModel):
    document_id: int
    changes: list[ProposedChange]
