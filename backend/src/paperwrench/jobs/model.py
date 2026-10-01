from datetime import datetime
from typing import Any
from typing import Generic
from typing import Literal
from typing import TypeVar

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from paperwrench.db.models import JobStatus
from paperwrench.db.models import JobType
from paperwrench.db.models import OperationStatus
from paperwrench.db.models import TargetStatus
from paperwrench.previews.model import ConfirmPreview


class CreateJob(ConfirmPreview):
    preview_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    acknowledge_external_race: bool = False


class CreateRollback(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preview_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    preview_token: str = Field(min_length=1, max_length=200)
    target_fingerprint: str
    result_fingerprint: str
    version: Literal[1]
    acknowledge: Literal[True]
    acknowledge_external_race: bool = False


class RollbackCounts(BaseModel):
    selected: int
    restored: int
    skipped: int
    conflicted: int


class JobView(BaseModel):
    id: int
    rule_id: int | None = None
    rule_revision: int | None = None
    rule_name: str | None = None
    type: JobType
    rollback_of_job_id: int | None
    rollback_job_id: int | None
    rollback_counts: RollbackCounts | None = None
    title: str
    status: JobStatus
    total: int
    processed: int
    counts: dict[str, int]
    source_kind: str | None
    dataset_query: dict[str, Any] | None
    operations: list[dict[str, Any]]
    preview: dict[str, Any] | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    resumable: bool


class TargetView(BaseModel):
    document_id: int
    position: int
    title: str | None
    excluded_operations: dict[str, str]
    status: TargetStatus
    error: str | None
    http_status: int | None
    attempts: int
    started_at: datetime | None
    finished_at: datetime | None


class RollbackCandidateView(BaseModel):
    document_id: int
    title: str | None
    status: Literal["available", "restored", "in_progress", "manual_review"]


class OperationView(BaseModel):
    id: int
    rollback_of_operation_id: int | None
    document_id: int
    field_kind: str
    field_key: str
    status: OperationStatus
    before: Any
    intended: Any
    written: Any
    rollback_candidate: bool
    error: str | None
    http_status: int | None
    attempts: int
    started_at: datetime | None
    finished_at: datetime | None


T = TypeVar("T")


class HistoryPage(BaseModel, Generic[T]):
    items: list[T]
    page: int
    page_size: int
    total: int
    page_count: int
