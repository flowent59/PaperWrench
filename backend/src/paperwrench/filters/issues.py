"""Structured diagnostics for filters, and the errors that carry them.

A FilterSet can fail in two genuinely different ways, and collapsing them
would make the UI unable to react correctly:

**Structurally invalid.** The expression does not describe a question at all:
an unknown field, an operator the field's type does not have, a value of the
wrong shape, an empty nested group. The user must change what they wrote.

**Valid but not compilable.** The expression describes a perfectly sensible
question that Paperless has no way to answer exactly - most commonly an OR
across a core field and a custom field. Nothing is wrong with the *filter*;
it is the server that cannot express it, and PaperWrench refuses rather than
approximating (ADR-0007).

``POST /api/v1/filters/validate`` returns both states separately for exactly
this reason: ``valid: true, compilable: false`` is a real and useful answer.

Every issue carries a ``path`` into the tree (``root.children[2].children[0]``)
so a builder UI can highlight the offending node instead of showing a wall of
text next to the submit button.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel

from paperwrench.errors import ErrorCode
from paperwrench.errors import PaperWrenchError


class FilterIssueStage(StrEnum):
    """Which of the two verdicts an issue belongs to."""

    VALIDATION = "validation"
    COMPILATION = "compilation"


class FilterIssueCode(StrEnum):
    """Stable, machine-readable reasons a filter was refused.

    Part of the API contract: the frontend switches on these to decide what
    to render, so they are renamed only with the same care as an
    :class:`~paperwrench.errors.ErrorCode`.
    """

    # -- structural / semantic validation ---------------------------------
    UNKNOWN_FIELD = "UNKNOWN_FIELD"
    OPERATOR_NOT_ALLOWED = "OPERATOR_NOT_ALLOWED"
    VALUE_REQUIRED = "VALUE_REQUIRED"
    VALUE_NOT_ALLOWED = "VALUE_NOT_ALLOWED"
    VALUE_WRONG_TYPE = "VALUE_WRONG_TYPE"
    VALUE_EMPTY = "VALUE_EMPTY"
    UNKNOWN_SELECT_OPTION = "UNKNOWN_SELECT_OPTION"
    EMPTY_GROUP = "EMPTY_GROUP"
    TOO_MANY_CONDITIONS = "TOO_MANY_CONDITIONS"
    TOO_DEEPLY_NESTED = "TOO_DEEPLY_NESTED"

    # -- compilability ----------------------------------------------------
    #: An OR whose branches touch both a core field and a custom field.
    #: Paperless composes query parameters with AND and evaluates custom
    #: fields in a separate expression, so this has no server-side form.
    MIXED_OR_UNSUPPORTED = "MIXED_OR_UNSUPPORTED"
    #: An OR over core fields only. Same root cause: DRF filter backends
    #: intersect, they do not union.
    CORE_OR_UNSUPPORTED = "CORE_OR_UNSUPPORTED"
    #: NOT is representable in the domain model but not compiled in M4.
    NEGATION_UNSUPPORTED = "NEGATION_UNSUPPORTED"
    #: Two conditions compile to the same Paperless query parameter with
    #: different values; only one would survive, silently.
    PARAMETER_CONFLICT = "PARAMETER_CONFLICT"
    #: Beyond Paperless's own custom_field_query limits (depth 10, 20 atoms).
    CUSTOM_FIELD_QUERY_TOO_COMPLEX = "CUSTOM_FIELD_QUERY_TOO_COMPLEX"


class FilterIssue(BaseModel):
    """One reason a filter was refused, addressed to a specific node."""

    stage: FilterIssueStage
    code: FilterIssueCode
    #: Dotted path to the offending node, e.g. ``root.children[1]``.
    path: str
    message: str
    #: The field reference key involved, when there is one
    #: (``core:title``, ``custom_field:17``).
    field: str | None = None
    operator: str | None = None
    details: dict[str, Any] | None = None


class FilterValidationError(PaperWrenchError):
    """The FilterSet is not structurally valid.

    422, because the request body describes something that is not a
    well-formed question.
    """

    status_code = 422
    code = ErrorCode.VALIDATION_ERROR

    def __init__(self, issues: list[FilterIssue]) -> None:
        first = issues[0].message if issues else "The filter is not valid."
        super().__init__(
            first,
            details={"issues": [issue.model_dump(mode="json") for issue in issues]},
        )
        self.issues = issues


class FilterNotCompilable(PaperWrenchError):
    """The FilterSet is valid but cannot be expressed as a Paperless query.

    **This is never a fallback point.** There is no client-side evaluation
    behind it, no "fetch a wider set and filter locally", no partial
    application of the conditions that did compile. The request stops here
    and nothing is sent to Paperless - see ADR-0007 and the guard test in
    ``tests/backend/unit/test_filter_no_fallback.py``.
    """

    status_code = 422
    code = ErrorCode.FILTER_NOT_COMPILABLE

    def __init__(self, issues: list[FilterIssue]) -> None:
        first = (
            issues[0].message
            if issues
            else "This filter cannot be expressed as a Paperless query."
        )
        super().__init__(
            first,
            details={"issues": [issue.model_dump(mode="json") for issue in issues]},
        )
        self.issues = issues
