"""Deterministic per-document proposals. No I/O, clock, client or registry."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from paperwrench.filters.model import CoreFieldRef
from paperwrench.filters.model import CustomFieldRef
from paperwrench.filters.model import FieldRef
from paperwrench.paperless.errors import PaperlessValidationError
from paperwrench.paperless.models import CustomField
from paperwrench.paperless.models import CustomFieldValue
from paperwrench.paperless.models import CustomFieldValueKind as Kind
from paperwrench.paperless.models import Document
from paperwrench.paperless.models import TypedCustomFieldValue
from paperwrench.paperless.mutations import core_payload
from paperwrench.paperless.value_validation import EDITABLE_CUSTOM_TYPES
from paperwrench.paperless.value_validation import validate_custom_present
from paperwrench.transformations.model import ClearOperation
from paperwrench.transformations.model import CoreValue
from paperwrench.transformations.model import EvaluationResult
from paperwrench.transformations.model import FieldValue
from paperwrench.transformations.model import Operation
from paperwrench.transformations.model import ProposedChange
from paperwrench.transformations.model import ReplaceOperation
from paperwrench.transformations.model import ResultStatus
from paperwrench.transformations.model import SetOperation
from paperwrench.transformations.model import TemplateOperation
from paperwrench.transformations.model import Transformation
from paperwrench.transformations.model import TransformationIssue

_TOKEN = re.compile(r"\{([^{}]+)\}")
_TEXT_CUSTOM_TYPES = frozenset({"string", "longtext"})
_CORE_WRITABLE = frozenset(
    {
        "title",
        "correspondent",
        "document_type",
        "storage_path",
        "tags",
        "created",
        "archive_serial_number",
    }
)
_CORE_TEMPLATE = frozenset({"title", "created", "added", "modified", "archive_serial_number"})


class _EvaluationError(Exception):
    def __init__(self, code: str, message: str, field_key: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.field_key = field_key


def _definition(field: FieldRef, definitions: dict[int, CustomField]) -> CustomField | None:
    if isinstance(field, CustomFieldRef):
        definition = definitions.get(field.field_id)
        if definition is None:
            raise _EvaluationError(
                "UNKNOWN_FIELD", "Custom-field definition is unavailable", field.key
            )
        if definition.data_type not in EDITABLE_CUSTOM_TYPES:
            raise _EvaluationError(
                "UNSUPPORTED_FIELD_TYPE", "Custom-field type is unsupported", field.key
            )
        return definition
    return None


def _read(field: FieldRef, document: Document, definition: CustomField | None) -> FieldValue:
    if isinstance(field, CoreFieldRef):
        raw = getattr(document, field.name.value)
        return CoreValue(kind=Kind.NULL if raw is None else Kind.PRESENT, raw=raw)
    entries = [entry for entry in document.custom_fields if entry.field == field.field_id]
    if len(entries) > 1:
        raise _EvaluationError(
            "DUPLICATE_FIELD", "Document has duplicate custom-field instances", field.key
        )
    entry = entries[0] if entries else None
    if definition is None:
        if entry is None:
            return CoreValue(kind=Kind.ABSENT)
        return CoreValue(
            kind=Kind.NULL if entry.value is None else Kind.PRESENT, raw=entry.value
        )
    return definition.typed_value(entry)


def _state(
    field: FieldRef, definition: CustomField | None, kind: Kind, raw: Any = None
) -> FieldValue:
    if isinstance(field, CoreFieldRef):
        return CoreValue(kind=kind, raw=raw)
    assert definition is not None
    entry = None if kind is Kind.ABSENT else CustomFieldValue(field=field.field_id, value=raw)
    return definition.typed_value(entry)


def _validate_present(field: FieldRef, value: Any, definition: CustomField | None) -> None:
    if value is None:
        raise _EvaluationError("INVALID_VALUE", "SET requires a non-null value", field.key)
    if isinstance(field, CoreFieldRef):
        if field.name.value not in _CORE_WRITABLE:
            raise _EvaluationError(
                "UNSUPPORTED_FIELD", "Core field cannot be transformed", field.key
            )
        try:
            core_payload({field.name.value: value})
        except PaperlessValidationError as exc:
            raise _EvaluationError("INVALID_VALUE", str(exc), field.key) from exc
    else:
        assert definition is not None
        try:
            validate_custom_present(value, definition)
        except PaperlessValidationError as exc:
            raise _EvaluationError("INVALID_VALUE", str(exc), field.key) from exc


def _render_value(field: FieldRef, document: Document, definitions: dict[int, CustomField]) -> str:
    definition = _definition(field, definitions)
    if isinstance(field, CoreFieldRef) and field.name.value not in _CORE_TEMPLATE:
        raise _EvaluationError(
            "UNSUPPORTED_FIELD", "Core field is not a template source", field.key
        )
    value = _read(field, document, definition)
    if value.kind is not Kind.PRESENT or value.raw == "":
        raise _EvaluationError(
            "TEMPLATE_UNRESOLVED", "Template source is absent, null or empty", field.key
        )
    if definition is not None:
        assert isinstance(value, TypedCustomFieldValue)
        if definition.data_type == "select":
            if value.select_option_id is None:
                raise _EvaluationError(
                    "TEMPLATE_UNRESOLVED", "Select option ID is invalid", field.key
                )
            label = value.select_label
            if label is None:
                raise _EvaluationError(
                    "TEMPLATE_UNRESOLVED", "Select option no longer exists", field.key
                )
            return label
        if definition.data_type == "monetary":
            if value.monetary is None:
                raise _EvaluationError(
                    "TEMPLATE_UNRESOLVED", "Monetary value is invalid", field.key
                )
            return str(value.monetary)
        if definition.data_type == "date":
            if not isinstance(value.raw, str):
                raise _EvaluationError("TEMPLATE_UNRESOLVED", "Date value is invalid", field.key)
            try:
                if date.fromisoformat(value.raw).isoformat() != value.raw:
                    raise ValueError
            except ValueError as exc:
                raise _EvaluationError(
                    "TEMPLATE_UNRESOLVED", "Date value is invalid", field.key
                ) from exc
    if isinstance(value.raw, bool):
        return "true" if value.raw else "false"
    if not isinstance(value.raw, (str, int)):
        raise _EvaluationError(
            "TEMPLATE_UNRESOLVED", "Template source has no stable text form", field.key
        )
    return str(value.raw)


def _template(
    operation: TemplateOperation, document: Document, definitions: dict[int, CustomField]
) -> str:
    used = _validate_template_shape(operation)
    rendered = {
        name: _render_value(operation.bindings[name], document, definitions)
        for name in sorted(used)
    }
    return _TOKEN.sub(lambda match: rendered[match.group(1)], operation.template)


def _validate_text_target(field: FieldRef, definition: CustomField | None) -> None:
    if (isinstance(field, CoreFieldRef) and field.name.value == "title") or (
        definition is not None and definition.data_type in _TEXT_CUSTOM_TYPES
    ):
        return
    raise _EvaluationError("INVALID_OPERATION", "Operation requires a text target", field.key)


def _validate_template_shape(operation: TemplateOperation) -> set[str]:
    tokens = _TOKEN.findall(operation.template)
    if "{" in _TOKEN.sub("", operation.template) or "}" in _TOKEN.sub("", operation.template):
        raise _EvaluationError("INVALID_TEMPLATE", "Unbalanced or nested template braces")
    used = set(tokens)
    if used != set(operation.bindings):
        raise _EvaluationError("INVALID_TEMPLATE", "Bindings must match placeholders exactly")
    return used


def _validate_static(
    operation: Operation, definitions: dict[int, CustomField]
) -> None:
    field = operation.field
    definition = _definition(field, definitions)
    if isinstance(operation, SetOperation):
        _validate_present(field, operation.value, definition)
    elif isinstance(operation, ClearOperation):
        if isinstance(field, CustomFieldRef):
            if operation.state is None:
                raise _EvaluationError(
                    "INVALID_CLEAR", "Custom clear requires absent or null", field.key
                )
        elif operation.state is not None or field.name.value not in {
            "correspondent", "document_type", "storage_path", "archive_serial_number", "tags"
        }:
            raise _EvaluationError(
                "INVALID_CLEAR", "Core field cannot be cleared this way", field.key
            )
    elif isinstance(operation, ReplaceOperation):
        _validate_text_target(field, definition)
    else:
        _validate_text_target(field, definition)
        for name in sorted(_validate_template_shape(operation)):
            source = operation.bindings[name]
            _definition(source, definitions)
            if isinstance(source, CoreFieldRef) and source.name.value not in _CORE_TEMPLATE:
                raise _EvaluationError(
                    "UNSUPPORTED_FIELD", "Core field is not a template source", source.key
                )


def validate(
    transformation: Transformation, definitions: dict[int, CustomField]
) -> list[TransformationIssue]:
    """Validate metadata-dependent operation rules without a document or I/O."""
    issues: list[TransformationIssue] = []
    for operation in transformation.operations:
        try:
            _validate_static(operation, definitions)
        except _EvaluationError as exc:
            issues.append(TransformationIssue(
                code=exc.code, message=str(exc), field_key=exc.field_key or operation.field.key
            ))
    return issues


def _intended(
    operation: SetOperation | ClearOperation | ReplaceOperation | TemplateOperation,
    before: FieldValue,
    definition: CustomField | None,
    document: Document,
    definitions: dict[int, CustomField],
) -> FieldValue:
    field = operation.field
    if isinstance(operation, SetOperation):
        _validate_present(field, operation.value, definition)
        return _state(field, definition, Kind.PRESENT, operation.value)
    if isinstance(operation, ClearOperation):
        if isinstance(field, CustomFieldRef):
            if operation.state is None:
                raise _EvaluationError(
                    "INVALID_CLEAR", "Custom clear requires absent or null", field.key
                )
            return _state(field, definition, Kind(operation.state))
        if operation.state is not None:
            raise _EvaluationError("INVALID_CLEAR", "Core clear does not take a state", field.key)
        if field.name.value in {
            "correspondent",
            "document_type",
            "storage_path",
            "archive_serial_number",
        }:
            return _state(field, definition, Kind.NULL)
        if field.name.value == "tags":
            return _state(field, definition, Kind.PRESENT, [])
        raise _EvaluationError("INVALID_CLEAR", "Core field cannot be cleared", field.key)
    if isinstance(operation, ReplaceOperation):
        _validate_text_target(field, definition)
        if before.kind is not Kind.PRESENT or not isinstance(before.raw, str):
            raise _EvaluationError(
                "INVALID_OPERATION", "Replace requires a present text value", field.key
            )
        value = before.raw.replace(operation.find, operation.replacement)
        _validate_present(field, value, definition)
        return _state(field, definition, Kind.PRESENT, value)
    _validate_text_target(field, definition)
    value = _template(operation, document, definitions)
    _validate_present(field, value, definition)
    return _state(field, definition, Kind.PRESENT, value)


def evaluate(
    document: Document, transformation: Transformation, definitions: dict[int, CustomField]
) -> EvaluationResult:
    """Evaluate every operation against the same unmodified document snapshot."""
    changes: list[ProposedChange] = []
    for operation in transformation.operations:
        before: FieldValue = CoreValue(kind=Kind.ABSENT)
        try:
            definition = (
                definitions.get(operation.field.field_id)
                if isinstance(operation.field, CustomFieldRef)
                else None
            )
            before = _read(operation.field, document, definition)
            _definition(operation.field, definitions)
            intended = _intended(operation, before, definition, document, definitions)
            status = ResultStatus.UNCHANGED if before == intended else ResultStatus.CHANGE
            changes.append(
                ProposedChange(
                    field=operation.field,
                    operation=operation.operation,
                    status=status,
                    before=before,
                    intended=intended,
                )
            )
        except _EvaluationError as exc:
            changes.append(
                ProposedChange(
                    field=operation.field,
                    operation=operation.operation,
                    status=ResultStatus.ERROR,
                    before=before,
                    issue=TransformationIssue(
                        code=exc.code,
                        message=str(exc),
                        field_key=exc.field_key,
                    ),
                )
            )
    return EvaluationResult(document_id=document.id, changes=changes)
