"""Preview contracts. No execution values or Job state."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from paperwrench.transformations.model import EvaluationResult
from paperwrench.transformations.model import ResultStatus
from paperwrench.transformations.model import Transformation
from paperwrench.transformations.model import TransformationIssue

PREVIEW_VERSION = 1
MAX_DOCUMENTS = 100_000
PAGE_SIZE = 100
MAX_BYTES = 128 * 1024 * 1024
TTL_SECONDS = 1800
MAX_PREVIEWS = 4
TIMEOUT_SECONDS = 300


class PreviewRow(EvaluationResult):
    title: str | None = None
    status: ResultStatus
    issue: TransformationIssue | None = None
    observed_revision: str | None = None
    catalog_revision: str | None = None
    rollback_spec: Transformation | None = None
    rollback_operation_ids: list[int] = Field(default_factory=list)
    excluded_operations: dict[str, str] = Field(default_factory=dict)


class PreviewSummary(BaseModel):
    id: str
    version: int = PREVIEW_VERSION
    created_at: datetime
    expires_at: datetime
    matched: int
    evaluated: int
    changed: int
    unchanged: int
    errors: int
    selection_fingerprint: str
    spec_fingerprint: str
    target_fingerprint: str
    result_fingerprint: str
    confirmed: bool = False
    rollback_of_job_id: int | None = None
    requires_external_race_ack: bool = False


class CreatedPreview(PreviewSummary):
    preview_token: str


class PreviewPage(BaseModel):
    items: list[PreviewRow]
    page: int
    page_size: int
    total: int
    page_count: int


class ConfirmPreview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preview_token: str = Field(min_length=1, max_length=200)
    transformation: Transformation
    target_fingerprint: str
    result_fingerprint: str
    version: Literal[1]
    acknowledge: Literal[True]
