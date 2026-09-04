"""Structural and semantic validation of a FilterSet.

This runs **before** the compiler and answers a narrower question: does this
expression describe a well-formed question about fields that exist, with
operators those fields have and values of the right shape?

It deliberately says nothing about whether Paperless can answer it. A filter
can pass every check here and still be refused by
:mod:`paperwrench.filters.compiler` - that is the two-verdict design described
in :mod:`paperwrench.filters.issues`.

Type checking is stricter than it strictly has to be, in three places where
leniency has already cost this project something:

* **Monetary values must be strings.** A JSON number reaching a Decimal
  comparison is how ``EUR0.10`` quietly becomes ``0.1000000000000000055``.
  M1 established Decimal-only handling for monetary custom fields; the filter
  engine does not get an exception.
* **Booleans are not integers.** Python says ``isinstance(True, int)``, so an
  unguarded integer check accepts ``true`` for an Integer field and compiles
  it to ``value_int__exact=True``. Every numeric check here rejects ``bool``
  explicitly.
* **Empty text values are refused outright.** VERIFIED_SOURCE (django-filter
  ``Filter.filter``): a filter whose value is in ``EMPTY_VALUES`` - which
  includes ``""`` - is **skipped entirely**, returning the queryset
  untouched. So ``title contains ""`` would not mean "titles containing
  nothing", it would mean *no title filter at all*, silently widening the
  result set. This is the same class of failure as M1's silently-ignored
  unknown parameter, and it is refused here rather than emitted. Users who
  want "this field holds an empty value" have ``is_empty``.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from decimal import InvalidOperation
from typing import Any

from paperwrench.filters.catalog import TEXT_SHAPED_TYPES
from paperwrench.filters.catalog import FieldCatalog
from paperwrench.filters.catalog import ResolvedField
from paperwrench.filters.catalog import UnknownFieldError
from paperwrench.filters.catalog import ValueShape
from paperwrench.filters.catalog import value_shape
from paperwrench.filters.issues import FilterIssue
from paperwrench.filters.issues import FilterIssueCode
from paperwrench.filters.issues import FilterIssueStage
from paperwrench.filters.model import LIST_OPERATORS
from paperwrench.filters.model import VALUELESS_OPERATORS
from paperwrench.filters.model import FilterCondition
from paperwrench.filters.model import FilterGroup
from paperwrench.filters.model import FilterNot
from paperwrench.filters.model import FilterOperator
from paperwrench.filters.model import FilterSet

#: Total leaf conditions allowed in one FilterSet. Well above anything a
#: human builds in the UI and well below anything that could be used to make
#: the compiler do pathological work. Paperless has its own, tighter limit
#: for the custom-field part (20 atoms), enforced by the compiler.
MAX_CONDITIONS = 50

#: Maximum group nesting. Mirrors Paperless's own custom_field_query depth
#: limit so a filter cannot pass validation only to be rejected for depth by
#: the compiler on the custom-field half.
MAX_DEPTH = 10

#: Dates are exchanged as plain ISO calendar days. ``date.fromisoformat`` on
#: 3.11 also accepts the compact ``20240102`` form and full timestamps; the
#: regex keeps the wire format to exactly one unambiguous shape.
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

#: A monetary value on the wire: an optional ISO-4217-ish currency prefix
#: followed by a signed decimal amount. Mirrors what Paperless itself accepts
#: for a monetary filter value (``MonetaryAmountField``), so anything this
#: rejects is something the server would have rejected too.
_MONETARY = re.compile(r"^(?P<currency>[A-Za-z]{3})?(?P<amount>-?\d+(?:\.\d+)?)$")


def parse_monetary(raw: str) -> Decimal:
    """Parse a wire monetary value to an exact :class:`~decimal.Decimal`.

    Accepts both ``"450.00"`` and ``"EUR450.00"``. Raises ``ValueError`` for
    anything else - notably for a value that arrived as a JSON number, which
    the caller must reject before getting here.
    """
    match = _MONETARY.match(raw.strip())
    if not match:
        raise ValueError(f"not a monetary value: {raw!r}")
    try:
        return Decimal(match.group("amount"))
    except InvalidOperation as exc:  # pragma: no cover - regex already guards
        raise ValueError(f"not a monetary value: {raw!r}") from exc


class _Validator:
    def __init__(self, catalog: FieldCatalog) -> None:
        self._catalog = catalog
        self.issues: list[FilterIssue] = []
        self._conditions = 0

    def add(
        self,
        code: FilterIssueCode,
        path: str,
        message: str,
        *,
        field: str | None = None,
        operator: FilterOperator | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.issues.append(
            FilterIssue(
                stage=FilterIssueStage.VALIDATION,
                code=code,
                path=path,
                message=message,
                field=field,
                operator=operator.value if operator is not None else None,
                details=details,
            )
        )

    # ----------------------------------------------------------- traversal
    def walk(
        self, node: FilterCondition | FilterGroup | FilterNot, path: str, depth: int
    ) -> None:
        if depth > MAX_DEPTH:
            self.add(
                FilterIssueCode.TOO_DEEPLY_NESTED,
                path,
                f"Filter groups may not nest more than {MAX_DEPTH} levels deep.",
            )
            return

        if isinstance(node, FilterCondition):
            self._conditions += 1
            if self._conditions > MAX_CONDITIONS:
                # Reported once, at the condition that crossed the line.
                if self._conditions == MAX_CONDITIONS + 1:
                    self.add(
                        FilterIssueCode.TOO_MANY_CONDITIONS,
                        path,
                        f"A filter may not contain more than {MAX_CONDITIONS} conditions.",
                    )
                return
            self.condition(node, path)
            return

        if isinstance(node, FilterNot):
            # NOT is structurally fine; it is the compiler that refuses it in
            # M4. Its child is validated normally so the user sees real value
            # errors rather than only the negation refusal.
            self.walk(node.child, f"{path}.child", depth + 1)
            return

        if not node.children:
            # An empty ROOT group means "no filter at all", which is a
            # legitimate state of the Explorer and compiles to no parameters.
            # An empty *nested* group has no defensible meaning - it is
            # neither "match everything" nor "match nothing" - and is almost
            # always a builder UI that added a group and no condition.
            if depth > 1:
                self.add(
                    FilterIssueCode.EMPTY_GROUP,
                    path,
                    "A nested group must contain at least one condition.",
                )
            return

        for index, child in enumerate(node.children):
            self.walk(child, f"{path}.children[{index}]", depth + 1)

    # ---------------------------------------------------------- conditions
    def condition(self, condition: FilterCondition, path: str) -> None:
        key = condition.field.key
        try:
            resolved = self._catalog.resolve(condition.field)
        except UnknownFieldError:
            self.add(
                FilterIssueCode.UNKNOWN_FIELD,
                path,
                (
                    f"{key} is not a field this instance can filter on. A custom "
                    "field referenced by id may have been deleted in Paperless."
                ),
                field=key,
                operator=condition.operator,
            )
            return

        if condition.operator not in resolved.operators:
            self.add(
                FilterIssueCode.OPERATOR_NOT_ALLOWED,
                path,
                (
                    f"{resolved.field_type.value} fields do not support "
                    f"{condition.operator.value!r}."
                ),
                field=key,
                operator=condition.operator,
                details={
                    "field_type": resolved.field_type.value,
                    "allowed": [op.value for op in resolved.operators],
                },
            )
            return

        self.value(condition, resolved, path)

    def value(self, condition: FilterCondition, resolved: ResolvedField, path: str) -> None:
        key = resolved.key
        operator = condition.operator
        raw = condition.value

        if operator in VALUELESS_OPERATORS:
            if raw is not None:
                self.add(
                    FilterIssueCode.VALUE_NOT_ALLOWED,
                    path,
                    f"{operator.value!r} takes no value.",
                    field=key,
                    operator=operator,
                )
            return

        if raw is None:
            self.add(
                FilterIssueCode.VALUE_REQUIRED,
                path,
                f"{operator.value!r} requires a value.",
                field=key,
                operator=operator,
            )
            return

        shape = value_shape(resolved.field_type, operator)

        if operator in LIST_OPERATORS:
            if not isinstance(raw, list):
                self.add(
                    FilterIssueCode.VALUE_WRONG_TYPE,
                    path,
                    f"{operator.value!r} takes a list of values.",
                    field=key,
                    operator=operator,
                )
                return
            if not raw:
                self.add(
                    FilterIssueCode.VALUE_EMPTY,
                    path,
                    (
                        f"{operator.value!r} needs at least one value; an empty "
                        "list has no defensible meaning."
                    ),
                    field=key,
                    operator=operator,
                )
                return
            for index, item in enumerate(raw):
                self.scalar(item, shape, resolved, f"{path}.value[{index}]", operator)
            return

        if isinstance(raw, list):
            self.add(
                FilterIssueCode.VALUE_WRONG_TYPE,
                path,
                f"{operator.value!r} takes a single value, not a list.",
                field=key,
                operator=operator,
            )
            return

        self.scalar(raw, shape, resolved, f"{path}.value", operator)

    def scalar(
        self,
        raw: Any,
        shape: ValueShape,
        resolved: ResolvedField,
        path: str,
        operator: FilterOperator,
    ) -> None:
        key = resolved.key

        def wrong(expected: str) -> None:
            self.add(
                FilterIssueCode.VALUE_WRONG_TYPE,
                path,
                f"{key} expects {expected}, got {type(raw).__name__}.",
                field=key,
                operator=operator,
                details={"expected": expected},
            )

        if shape is ValueShape.TEXT:
            if not isinstance(raw, str):
                wrong("a string")
                return
            if not raw:
                # See the module docstring: an empty value makes django-filter
                # drop the whole filter, silently widening the result set.
                self.add(
                    FilterIssueCode.VALUE_EMPTY,
                    path,
                    (
                        "An empty text value is not a filter: Paperless would "
                        "ignore the condition entirely and return every "
                        "document. Use 'is empty' or 'is missing' instead."
                    ),
                    field=key,
                    operator=operator,
                )
            return

        if shape is ValueShape.INTEGER:
            # bool is a subclass of int; True must not pass as an integer.
            if isinstance(raw, bool) or not isinstance(raw, int):
                wrong("an integer")
            return

        if shape is ValueShape.FLOAT:
            if isinstance(raw, bool) or not isinstance(raw, int | float):
                wrong("a number")
            return

        if shape is ValueShape.DECIMAL:
            if not isinstance(raw, str):
                wrong("an exact decimal as a string (a JSON number would lose precision)")
                return
            try:
                parse_monetary(raw)
            except ValueError:
                self.add(
                    FilterIssueCode.VALUE_WRONG_TYPE,
                    path,
                    (
                        f"{raw!r} is not a monetary amount. Expected a decimal "
                        'string such as "0.00" or "EUR1234.56".'
                    ),
                    field=key,
                    operator=operator,
                )
            return

        if shape is ValueShape.DATE:
            if not isinstance(raw, str) or not _ISO_DATE.match(raw):
                wrong("a date as YYYY-MM-DD")
                return
            try:
                date.fromisoformat(raw)
            except ValueError:
                self.add(
                    FilterIssueCode.VALUE_WRONG_TYPE,
                    path,
                    f"{raw!r} is not a real calendar date.",
                    field=key,
                    operator=operator,
                )
            return

        if shape is ValueShape.BOOLEAN:
            if not isinstance(raw, bool):
                wrong("true or false")
            return

        if shape is ValueShape.SELECT_OPTION:
            if not isinstance(raw, str):
                wrong("a select option id")
                return
            if raw not in resolved.select_option_ids:
                # Refusing an unknown option id also refuses a *label* sent in
                # its place. Paperless would have accepted the label and
                # resolved it (CustomFieldQueryParser.SelectField), which
                # sounds helpful but means a renamed option silently changes
                # which documents a saved filter matches. The stored id is the
                # identity; nothing else is.
                self.add(
                    FilterIssueCode.UNKNOWN_SELECT_OPTION,
                    path,
                    (
                        f"{raw!r} is not an option of {resolved.label!r}. Filters "
                        "store the option id, never its label."
                    ),
                    field=key,
                    operator=operator,
                    details={"known_option_ids": sorted(resolved.select_option_ids)},
                )
            return

        if shape is ValueShape.REFERENCE_ID:
            if isinstance(raw, bool) or not isinstance(raw, int):
                wrong("a metadata object id")
                return
            if raw <= 0:
                self.add(
                    FilterIssueCode.VALUE_WRONG_TYPE,
                    path,
                    f"{raw} is not a valid object id.",
                    field=key,
                    operator=operator,
                )
            return

        # ValueShape.NONE is handled by the VALUELESS_OPERATORS branch above.
        raise AssertionError(f"unhandled value shape {shape}")  # pragma: no cover


def validate_filterset(filterset: FilterSet, catalog: FieldCatalog) -> list[FilterIssue]:
    """Every structural/semantic problem with ``filterset``.

    Returns an empty list when the filter is valid. Reports *all* the issues
    it can find rather than stopping at the first, so a builder UI can flag
    every bad row at once.
    """
    validator = _Validator(catalog)
    validator.walk(filterset.root, "root", depth=1)
    return validator.issues


__all__ = [
    "MAX_CONDITIONS",
    "MAX_DEPTH",
    "TEXT_SHAPED_TYPES",
    "parse_monetary",
    "validate_filterset",
]
