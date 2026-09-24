"""Inspector validation and mutation service, reusing the M2 boundary."""

from __future__ import annotations

import hashlib
import json
from typing import Any
from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from paperwrench.paperless.client import PaperlessClient
from paperwrench.paperless.errors import PaperlessConflictError
from paperwrench.paperless.errors import PaperlessValidationError
from paperwrench.paperless.models import CustomField
from paperwrench.paperless.models import MetadataKind
from paperwrench.paperless.mutations import CorePatch
from paperwrench.paperless.mutations import MutationResult
from paperwrench.paperless.mutations import core_payload
from paperwrench.paperless.registry import MetadataNotFoundError
from paperwrench.paperless.registry import MetadataRegistry
from paperwrench.paperless.value_validation import EDITABLE_CUSTOM_TYPES
from paperwrench.paperless.value_validation import validate_custom_present


class CustomChange(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    field_id: int = Field(gt=0)
    kind: Literal["absent", "null", "present"]
    value: Any = None


class EditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    catalog_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    core: CorePatch = Field(default_factory=CorePatch)
    custom_changes: list[CustomChange] = Field(default_factory=list, max_length=100)
    acknowledge_external_race: bool = False


def catalog_revision(fields: list[CustomField]) -> str:
    return hashlib.sha256(
        json.dumps(
            [field.model_dump(mode="json") for field in sorted(fields, key=lambda f: f.id)],
            sort_keys=True,
            ensure_ascii=False,
        ).encode()
    ).hexdigest()


def validate_custom_change(change: CustomChange, definition: CustomField) -> None:
    if definition.data_type not in EDITABLE_CUSTOM_TYPES:
        raise PaperlessValidationError("This custom-field type is read-only in M5.")
    value = change.value
    if change.kind != "present":
        if value is not None:
            raise PaperlessValidationError("Absent/null operations cannot carry a value.")
        return
    if "value" not in change.model_fields_set or value is None:
        raise PaperlessValidationError("A present operation requires an explicit non-null value.")
    validate_custom_present(value, definition)


async def edit_document(
    document_id: int,
    request: EditRequest,
    client: PaperlessClient,
    registry: MetadataRegistry,
) -> MutationResult:
    core = core_payload(request.core.model_dump(exclude_unset=True))
    if len({change.field_id for change in request.custom_changes}) != len(request.custom_changes):
        raise PaperlessValidationError("A custom field may appear only once per mutation.")
    updates: list[dict[str, Any]] = []
    removals: list[int] = []
    if request.custom_changes:
        client._require_race_ack(request.acknowledge_external_race)
        await registry.refresh(MetadataKind.CUSTOM_FIELD)
        definitions = await registry.all_custom_fields()
        if catalog_revision(definitions) != request.catalog_revision:
            raise PaperlessConflictError("Custom-field definitions changed; reload before saving.")
        for change in request.custom_changes:
            try:
                definition = await registry.custom_field_by_id(change.field_id)
            except MetadataNotFoundError as exc:
                raise PaperlessValidationError("Custom field is unavailable.") from exc
            validate_custom_change(change, definition)
            if change.kind == "absent":
                removals.append(change.field_id)
            else:
                updates.append({"field": change.field_id, "value": change.value})
    for key, kind, lookup in [
        ("correspondent", MetadataKind.CORRESPONDENT, registry.correspondent_by_id),
        ("document_type", MetadataKind.DOCUMENT_TYPE, registry.document_type_by_id),
        ("storage_path", MetadataKind.STORAGE_PATH, registry.storage_path_by_id),
    ]:
        if core.get(key) is not None:
            await registry.refresh(kind)
            try:
                await lookup(core[key])
            except MetadataNotFoundError as exc:
                raise PaperlessValidationError("Referenced metadata is unavailable.") from exc
    if "tags" in core:
        if len(set(core["tags"])) != len(core["tags"]):
            raise PaperlessValidationError("Tag IDs must be unique.")
        await registry.refresh(MetadataKind.TAG)
        for tag_id in core["tags"]:
            try:
                await registry.tag_by_id(tag_id)
            except MetadataNotFoundError as exc:
                raise PaperlessValidationError("Referenced tag is unavailable.") from exc
    return await client.mutate_document(
        document_id,
        expected_revision=request.expected_revision,
        core=core,
        custom_updates=updates,
        remove_custom_fields=removals,
        acknowledge_external_race=request.acknowledge_external_race,
    )
