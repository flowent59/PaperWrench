"""Schema validation and pure read-only rule evaluation."""

from __future__ import annotations

from contextlib import suppress
from datetime import date
from decimal import Decimal
from decimal import InvalidOperation
from typing import Any

from paperwrench.errors import ErrorCode
from paperwrench.errors import PaperWrenchError
from paperwrench.filters.catalog import FieldCatalog
from paperwrench.filters.catalog import FieldType
from paperwrench.filters.catalog import ResolvedField
from paperwrench.filters.catalog import UnknownFieldError
from paperwrench.filters.model import CoreFieldRef
from paperwrench.filters.model import CustomFieldRef
from paperwrench.paperless.models import CustomField
from paperwrench.paperless.models import CustomFieldValueKind as Kind
from paperwrench.paperless.models import Document
from paperwrench.schemas.model import DocumentSchemaResult
from paperwrench.schemas.model import EqualsRule
from paperwrench.schemas.model import RuleResult
from paperwrench.schemas.model import RuleStatus
from paperwrench.schemas.model import SchemaDefinition
from paperwrench.schemas.model import SchemaRule


def _invalid(message: str, *, field: str | None = None) -> PaperWrenchError:
    return PaperWrenchError(
        message,
        status_code=422,
        code=ErrorCode.VALIDATION_ERROR,
        details={"field": field} if field else None,
    )


def _expected(rule: EqualsRule, resolved: ResolvedField) -> Any:
    """Validate an equals literal without coercion; money stays Decimal."""
    value = rule.value
    kind = resolved.field_type
    if kind in {FieldType.TEXT, FieldType.LONG_TEXT, FieldType.URL}:
        if isinstance(value, str):
            return value
    elif kind is FieldType.MONETARY:
        if isinstance(value, str):
            try:
                amount = Decimal(value)
                if amount.is_finite():
                    return amount
            except InvalidOperation:
                pass
    elif kind is FieldType.BOOLEAN:
        if type(value) is bool:
            return value
    elif kind in {FieldType.INTEGER, FieldType.REFERENCE}:
        if type(value) is int:
            return value
    elif kind is FieldType.FLOAT:
        if type(value) in {int, float}:
            return float(value)
    elif kind in {FieldType.DATE, FieldType.DATETIME}:
        if isinstance(value, str):
            try:
                return date.fromisoformat(value)
            except ValueError:
                pass
    elif (
        kind is FieldType.SELECT
        and isinstance(value, str)
        and value in resolved.select_option_ids
    ):
        return value
    raise _invalid(
        f"Invalid equals value for {rule.field.key} ({kind.value}).", field=rule.field.key
    )


def validate_rules(rules: list[SchemaRule], catalog: FieldCatalog) -> None:
    for rule in rules:
        try:
            resolved = catalog.resolve(rule.field)
        except UnknownFieldError as exc:
            raise _invalid(
                f"Field {rule.field.key} no longer exists.", field=rule.field.key
            ) from exc
        if resolved.field_type is not rule.field_type:
            raise _invalid(
                f"Field {rule.field.key} changed type from {rule.field_type.value} "
                f"to {resolved.field_type.value}.",
                field=rule.field.key,
            )
        if isinstance(rule, EqualsRule):
            if kind_not_scalar(resolved.field_type):
                raise _invalid(f"Equals is unsupported for {rule.field.key}.", field=rule.field.key)
            _expected(rule, resolved)


def kind_not_scalar(kind: FieldType) -> bool:
    return kind in {FieldType.TAG_SET, FieldType.DOCUMENT_LINK}


def validate_definition(schema: SchemaDefinition, catalog: FieldCatalog) -> None:
    if not schema.name.strip():
        raise _invalid("Schema name cannot be blank.")
    validate_rules(schema.rules, catalog)


def _actual(
    rule: SchemaRule, document: Document, definitions: dict[int, CustomField]
) -> tuple[Kind, Any]:
    if isinstance(rule.field, CoreFieldRef):
        raw = getattr(document, rule.field.name.value)
        if raw is None:
            return Kind.NULL, None
        return Kind.PRESENT, raw
    assert isinstance(rule.field, CustomFieldRef)
    entries = [entry for entry in document.custom_fields if entry.field == rule.field.field_id]
    if len(entries) > 1:
        raise _invalid(f"Duplicate values for {rule.field.key}.", field=rule.field.key)
    typed = definitions[rule.field.field_id].typed_value(entries[0] if entries else None)
    if rule.field_type is FieldType.MONETARY and typed.kind is Kind.PRESENT:
        return typed.kind, typed.monetary.amount if typed.monetary else typed.raw
    if rule.field_type is FieldType.SELECT and typed.kind is Kind.PRESENT:
        return typed.kind, typed.select_option_id if typed.select_option_id else typed.raw
    return typed.kind, typed.raw


def evaluate_document(
    schema: SchemaDefinition, document: Document, definitions: dict[int, CustomField]
) -> DocumentSchemaResult:
    results: list[RuleResult] = []
    for index, rule in enumerate(schema.rules):
        kind, actual = _actual(rule, document, definitions)
        present = kind is Kind.PRESENT and actual != "" and actual != []
        expected: Any = None
        if isinstance(rule, EqualsRule):
            # The same validation was already performed before the Paperless GET.
            # Decimal from the literal is compared to the typed monetary amount.
            expected = Decimal(rule.value) if rule.field_type is FieldType.MONETARY else rule.value
            if rule.field_type is FieldType.FLOAT:
                expected = float(rule.value)
            if rule.field_type in {FieldType.DATE, FieldType.DATETIME}:
                expected = date.fromisoformat(rule.value)
            if rule.field_type in {FieldType.DATE, FieldType.DATETIME} and isinstance(actual, str):
                with suppress(ValueError):
                    actual = date.fromisoformat(actual[:10])
            passed = kind is Kind.PRESENT and type(actual) is type(expected) and actual == expected
        else:
            passed = present
        results.append(
            RuleResult(
                rule_index=index,
                field=rule.field,
                kind=rule.kind,
                status=RuleStatus.PASS if passed else RuleStatus.FAIL,
                value_kind=kind,
                actual=actual,
                expected=expected,
                code=None if passed else ("REQUIRED" if rule.kind == "required" else "NOT_EQUAL"),
            )
        )
    return DocumentSchemaResult(
        document_id=document.id,
        title=document.title,
        status=(
            RuleStatus.PASS
            if all(result.status is RuleStatus.PASS for result in results)
            else RuleStatus.FAIL
        ),
        rules=results,
    )
