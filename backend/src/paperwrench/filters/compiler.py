"""FilterSet -> Paperless query parameters, or an explicit refusal.

This is the module ADR-0007 is about. Two rules govern everything in it:

**1. Nothing is forwarded that has not been proven to mean something.**
Paperless-ngx 3.1.2 silently ignores an unknown filter parameter and returns
the full, unfiltered result set with HTTP 200 (VERIFIED_LIVE, M1 -
``docs/paperless-api.md`` §6). There is therefore no server-side signal
distinguishing "filter applied" from "filter discarded", and a typo in a saved
FilterSet would widen a transformation from twelve documents to the entire
library while every response looked successful. Every parameter this compiler
emits comes from a table that was read in the 3.1.2 source.

**2. If it cannot be compiled, it is refused - never approximated.**
:class:`~paperwrench.filters.issues.FilterNotCompilable` is raised and **no
request is made**. There is no client-side evaluation behind this door: no
"fetch a wider set and filter in Python", no partial application of the
conditions that did compile, no capped fetch. A filter engine that quietly
falls back to local filtering produces a wrong count, and the count is the
number a user reads before pressing a destructive button.

What the compiler can express
-----------------------------

* Any number of **core** conditions, ANDed. Paperless composes query
  parameters with AND (each django-filter filter narrows the queryset in
  turn), so this is exact.
* Any number of **custom-field** conditions, in arbitrary AND/OR nesting,
  compiled into the single ``custom_field_query`` parameter - Paperless's own
  nested boolean expression language (VERIFIED_SOURCE:
  ``CustomFieldQueryParser``), within its documented limits of depth 10 and
  20 atoms.
* Core and custom-field conditions combined with **AND**, since the
  ``custom_field_query`` parameter is itself just another parameter and
  therefore intersects with the rest.

What it refuses, and why
------------------------

* **OR across core fields.** DRF filter backends intersect; there is no union
  form. ``CORE_OR_UNSUPPORTED``.
* **OR mixing a core field with a custom field.** The custom-field expression
  is evaluated separately and can only intersect with the core parameters.
  ``MIXED_OR_UNSUPPORTED``.
* **NOT.** Representable in the domain model, not compiled in M4 - see
  ADR-0011. ``NEGATION_UNSUPPORTED``.
* **Two conditions that collapse onto the same query parameter with
  different values.** Only one would survive (a query string carries one
  value per key), so the other would be silently dropped - the exact failure
  this engine exists to prevent. ``PARAMETER_CONFLICT``.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel
from pydantic import Field

from paperwrench.filters.catalog import FieldCatalog
from paperwrench.filters.catalog import FieldType
from paperwrench.filters.catalog import ResolvedField
from paperwrench.filters.catalog import UnknownFieldError
from paperwrench.filters.issues import FilterIssue
from paperwrench.filters.issues import FilterIssueCode
from paperwrench.filters.issues import FilterIssueStage
from paperwrench.filters.issues import FilterNotCompilable
from paperwrench.filters.model import FilterCondition
from paperwrench.filters.model import FilterGroup
from paperwrench.filters.model import FilterNot
from paperwrench.filters.model import FilterOperator
from paperwrench.filters.model import FilterSet
from paperwrench.filters.model import GroupOperator
from paperwrench.filters.validation import parse_monetary

#: VERIFIED_SOURCE (3.1.2, ``documents/filters.py``):
#: ``CUSTOM_FIELD_QUERY_MAX_DEPTH`` / ``CUSTOM_FIELD_QUERY_MAX_ATOMS``.
#: Enforced here as well as upstream so an over-complex filter is refused
#: locally, with a structured issue, instead of costing a round trip to
#: collect a 400.
CUSTOM_FIELD_QUERY_MAX_DEPTH = 10
CUSTOM_FIELD_QUERY_MAX_ATOMS = 20

#: Query parameters whose value is a comma-separated id list and whose
#: repetition genuinely means "and also".
#:
#: VERIFIED_SOURCE (``ObjectFilter.filter``): ``tags__id__all`` loops
#: ``qs.filter(tags__id=x)`` per id and ``tags__id__none`` loops
#: ``qs.exclude(tags__id=x)``, so two separate conditions on either are
#: exactly equivalent to one condition over the union of their ids. That is
#: why these merge instead of conflicting.
#:
#: ``tags__id__in`` is deliberately absent: it compiles to a single
#: ``tags__id__in=[...]``, so "any of {A,B}" AND "any of {C,D}" is *not*
#: "any of {A,B,C,D}". Merging it would silently broaden the filter.
_UNIONABLE_ID_LIST_PARAMS = frozenset({"tags__id__all", "tags__id__none"})


class PaperlessQuery(BaseModel):
    """The exact query PaperWrench will send, and nothing else.

    ``params`` is the literal parameter mapping handed to the Paperless
    client - strings, because that is what ends up on the wire, so what is
    recorded is what was sent. Stored alongside a job in later milestones
    (ADR-0007) so a history entry can show precisely what was asked.

    ``custom_field_expression`` is the same information the
    ``custom_field_query`` parameter carries, kept in structured form for
    explanation and for tests to assert on without re-parsing JSON.
    """

    params: dict[str, str] = Field(default_factory=dict)
    custom_field_expression: Any = None

    @property
    def is_unfiltered(self) -> bool:
        """True when this query constrains nothing.

        Worth being able to ask explicitly: "no filter" is a legitimate state
        of the Explorer and a dangerous one for a transformation.
        """
        return not self.params


# --------------------------------------------------------------- normalise


def _normalise(
    node: FilterCondition | FilterGroup | FilterNot,
) -> FilterCondition | FilterGroup | FilterNot:
    """Rewrite a subtree into its simplest equivalent form.

    Two rewrites, both exact rather than heuristic:

    * a group with exactly one child *is* that child, whatever its operator
      (``OR(x) == AND(x) == x``);
    * a group nested directly inside a group with the same operator is
      flattened into it (associativity).

    This is what makes ``AND(a, AND(b, c))`` compile rather than being
    refused for a shape difference the user never intended, and it is applied
    to a fixed point so the two rewrites can enable each other.
    """
    if isinstance(node, FilterCondition):
        return node
    if isinstance(node, FilterNot):
        return FilterNot(child=_normalise(node.child))

    children = [_normalise(child) for child in node.children]

    flattened: list[Any] = []
    for child in children:
        if isinstance(child, FilterGroup) and child.operator is node.operator:
            flattened.extend(child.children)
        else:
            flattened.append(child)

    if len(flattened) == 1:
        return _normalise(flattened[0])
    return FilterGroup(operator=node.operator, children=flattened)


def _leaf_sources(node: FilterCondition | FilterGroup | FilterNot) -> set[bool]:
    """The set of ``is_custom`` flags of every leaf under ``node``.

    ``{True}`` = custom fields only, ``{False}`` = core only, both = mixed.
    Uses the reference's own shape rather than the catalog so it works even
    for a field the catalog does not know (that case is already a validation
    error; this must not crash on the way to reporting it).
    """
    if isinstance(node, FilterCondition):
        return {node.field.source.value == "custom_field"}
    if isinstance(node, FilterNot):
        return _leaf_sources(node.child)
    found: set[bool] = set()
    for child in node.children:
        found |= _leaf_sources(child)
    return found


# ------------------------------------------------------------ core mapping


def _fmt_date(value: Any) -> str:
    return str(value)


def _fmt_id_list(values: Any) -> str:
    """A comma-separated id list, as ``ObjectFilter`` expects it.

    Sorted and de-duplicated so the same logical condition always produces
    byte-identical parameters - which matters because those parameters are
    recorded, hashed and compared.
    """
    return ",".join(str(item) for item in sorted({int(item) for item in values}))


def _compile_core_condition(
    condition: FilterCondition, resolved: ResolvedField
) -> dict[str, str]:
    """One core condition -> the Paperless parameters that express it exactly.

    Every mapping below comes from ``DocumentFilterSet`` in the 3.1.2 source.
    ``EQUALS`` on a date deliberately produces *two* parameters: neither
    ``DATE_KWARGS`` nor ``DATETIME_KWARGS`` includes an ``exact`` lookup, and
    ``>= d AND <= d`` on a day is exactly ``== d`` - an exact compilation, not
    an approximation.
    """
    assert resolved.core_field is not None
    name = resolved.core_field
    op = condition.operator
    value = condition.value

    if resolved.field_type is FieldType.TEXT:
        # CHAR_KWARGS: istartswith / iendswith / icontains / iexact.
        lookup = {
            FilterOperator.CONTAINS: "icontains",
            FilterOperator.EQUALS: "iexact",
            FilterOperator.STARTS_WITH: "istartswith",
            FilterOperator.ENDS_WITH: "iendswith",
        }[op]
        return {f"{name.value}__{lookup}": str(value)}

    if resolved.field_type is FieldType.REFERENCE:
        if op is FilterOperator.EQUALS:
            return {f"{name.value}__id": str(value)}
        if op is FilterOperator.NOT_EQUALS:
            return {f"{name.value}__id__none": str(value)}
        if op is FilterOperator.IN:
            return {f"{name.value}__id__in": _fmt_id_list(value)}
        # Meta.fields declares {"correspondent": ["isnull"]} & co.
        return {f"{name.value}__isnull": "true" if op is FilterOperator.IS_MISSING else "false"}

    if resolved.field_type is FieldType.TAG_SET:
        if op is FilterOperator.HAS_ALL_OF:
            return {"tags__id__all": _fmt_id_list(value)}
        if op is FilterOperator.HAS_ANY_OF:
            return {"tags__id__in": _fmt_id_list(value)}
        if op is FilterOperator.HAS_NONE_OF:
            return {"tags__id__none": _fmt_id_list(value)}
        # is_tagged is a BooleanFilter on `tags__isnull`, excluded - i.e.
        # "has at least one tag". is_missing is therefore is_tagged=false.
        return {"is_tagged": "false" if op is FilterOperator.IS_MISSING else "true"}

    if resolved.field_type is FieldType.DATE:
        # `created` is a DateField: plain gt/gte/lt/lte compare days.
        if op is FilterOperator.EQUALS:
            return {
                f"{name.value}__gte": _fmt_date(value),
                f"{name.value}__lte": _fmt_date(value),
            }
        lookup = {
            FilterOperator.GREATER_THAN: "gt",
            FilterOperator.GREATER_OR_EQUAL: "gte",
            FilterOperator.LESS_THAN: "lt",
            FilterOperator.LESS_OR_EQUAL: "lte",
        }[op]
        return {f"{name.value}__{lookup}": _fmt_date(value)}

    if resolved.field_type is FieldType.DATETIME:
        # `added`/`modified` are DateTimeFields. Using the `date__*` variants
        # (present in DATETIME_KWARGS) keeps the comparison day-granular, so
        # "added <= 2024-03-01" includes everything added that day rather
        # than only what arrived before midnight.
        if op is FilterOperator.EQUALS:
            return {
                f"{name.value}__date__gte": _fmt_date(value),
                f"{name.value}__date__lte": _fmt_date(value),
            }
        lookup = {
            FilterOperator.GREATER_THAN: "date__gt",
            FilterOperator.GREATER_OR_EQUAL: "date__gte",
            FilterOperator.LESS_THAN: "date__lt",
            FilterOperator.LESS_OR_EQUAL: "date__lte",
        }[op]
        return {f"{name.value}__{lookup}": _fmt_date(value)}

    if resolved.field_type is FieldType.INTEGER:
        # INT_KWARGS: exact/gt/gte/lt/lte/isnull. django-filter strips the
        # `__exact` suffix, so equality is the bare field name.
        if op is FilterOperator.EQUALS:
            return {name.value: str(value)}
        if op in (FilterOperator.IS_MISSING, FilterOperator.IS_PRESENT):
            return {
                f"{name.value}__isnull": "true" if op is FilterOperator.IS_MISSING else "false"
            }
        lookup = {
            FilterOperator.GREATER_THAN: "gt",
            FilterOperator.GREATER_OR_EQUAL: "gte",
            FilterOperator.LESS_THAN: "lt",
            FilterOperator.LESS_OR_EQUAL: "lte",
        }[op]
        return {f"{name.value}__{lookup}": str(value)}

    raise AssertionError(  # pragma: no cover - every core type is handled
        f"no core mapping for {resolved.key} ({resolved.field_type})"
    )


# ---------------------------------------------------- custom field mapping

_CUSTOM_LOOKUPS: dict[FilterOperator, str] = {
    FilterOperator.EQUALS: "exact",
    FilterOperator.IN: "in",
    FilterOperator.CONTAINS: "icontains",
    FilterOperator.STARTS_WITH: "istartswith",
    FilterOperator.ENDS_WITH: "iendswith",
    FilterOperator.GREATER_THAN: "gt",
    FilterOperator.GREATER_OR_EQUAL: "gte",
    FilterOperator.LESS_THAN: "lt",
    FilterOperator.LESS_OR_EQUAL: "lte",
}


def _custom_value(resolved: ResolvedField, raw: Any) -> Any:
    """Normalise one custom-field value for ``custom_field_query``.

    Monetary is the interesting case. Paperless compares against
    ``value_monetary_amount``, a generated column that strips a leading
    three-character currency code, and its ``MonetaryAmountField`` re-applies
    the same heuristic to the *filter* value ("if it does not start with a
    digit or a minus, drop three characters"). Sending a bare, normalised
    decimal string sidesteps that heuristic entirely: there is no prefix to
    guess at, and ``Decimal`` -> ``str`` preserves the exact value including
    a trailing ``.00`` - so ``EUR0.00`` arrives as ``0.00`` and stays a real,
    non-null, comparable zero rather than turning into a float.
    """
    if resolved.field_type is FieldType.MONETARY:
        return str(parse_monetary(str(raw)))
    return raw


def _custom_atom(condition: FilterCondition, resolved: ResolvedField) -> Any:
    """One custom-field condition -> a ``custom_field_query`` expression.

    Almost every operator becomes a single atom ``[id, lookup, value]``.
    ``IS_EMPTY`` is the exception: it expands to a two-atom OR, because
    "empty" is genuinely two states on the Paperless side.
    """
    field_id = resolved.custom_field_id
    op = condition.operator

    if op is FilterOperator.IS_MISSING:
        # `exists` counts field *instances* on the document, so exists=false
        # is exactly ABSENT - the field is not attached at all.
        return [field_id, "exists", False]
    if op is FilterOperator.IS_PRESENT:
        return [field_id, "exists", True]
    if op is FilterOperator.IS_NULL:
        # `isnull` is evaluated as `has_field AND value IS NULL`, so it can
        # never match an ABSENT document. That asymmetry with `exists` is the
        # whole reason NULL and ABSENT stay separable here.
        return [field_id, "isnull", True]
    if op is FilterOperator.HAS_VALUE:
        return [field_id, "isnull", False]
    if op is FilterOperator.IS_EMPTY:
        # NULL *or* the empty string - and deliberately NOT exists=false.
        # See ADR-0011: a document that never had the field is a different
        # thing from one that has it and left it blank, and collapsing them
        # is the conflation M4 exists to prevent.
        return ["OR", [[field_id, "isnull", True], [field_id, "exact", ""]]]

    lookup = _CUSTOM_LOOKUPS[op]
    if op is FilterOperator.IN:
        return [field_id, lookup, [_custom_value(resolved, item) for item in condition.value]]
    return [field_id, lookup, _custom_value(resolved, condition.value)]


#
# Both counters below assume a compiled expression contains only atoms and
# n-ary AND/OR nodes. That holds because the compiler refuses ``NOT``
# outright (see ``refuse_negation``), so no ``["NOT", expr]`` can reach here.
# If NOT is ever compiled, both must learn to descend into it - a unary node
# counted as a leaf would under-report depth and atoms, which is the one
# direction that matters, since these guard an upstream limit.


def _expression_depth(expr: Any) -> int:
    """Depth as Paperless's parser counts it.

    ``CustomFieldQueryParser._parse_expr`` increments the depth counter once
    per expression node - atoms included - so an atom is depth 1 and a
    logical node is one more than its deepest child.
    """
    if isinstance(expr, list) and len(expr) == 2 and isinstance(expr[0], str):
        return 1 + max(_expression_depth(item) for item in expr[1])
    return 1


def _expression_atoms(expr: Any) -> int:
    """Number of rule-1/2/3 atoms, which is what Paperless caps at 20."""
    if isinstance(expr, list) and len(expr) == 2 and isinstance(expr[0], str):
        return sum(_expression_atoms(item) for item in expr[1])
    return 1


# ------------------------------------------------------------- the compiler


class _Compiler:
    def __init__(self, catalog: FieldCatalog) -> None:
        self._catalog = catalog
        self.issues: list[FilterIssue] = []
        self.params: dict[str, str] = {}
        self.custom: list[Any] = []

    def refuse(
        self,
        code: FilterIssueCode,
        path: str,
        message: str,
        *,
        field: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.issues.append(
            FilterIssue(
                stage=FilterIssueStage.COMPILATION,
                code=code,
                path=path,
                message=message,
                field=field,
                details=details,
            )
        )

    # ------------------------------------------------------------- params
    def merge(self, new: dict[str, str], path: str, field: str) -> None:
        for key, value in new.items():
            existing = self.params.get(key)
            if existing is None:
                self.params[key] = value
                continue
            if existing == value:
                # The same condition twice. Idempotent, so nothing is lost.
                continue
            if key in _UNIONABLE_ID_LIST_PARAMS:
                merged = sorted(
                    {int(item) for item in existing.split(",")}
                    | {int(item) for item in value.split(",")}
                )
                self.params[key] = ",".join(str(item) for item in merged)
                continue
            self.refuse(
                FilterIssueCode.PARAMETER_CONFLICT,
                path,
                (
                    f"Two conditions both compile to the Paperless parameter "
                    f"{key!r} with different values ({existing!r} and {value!r}). "
                    "A query string carries one value per parameter, so one of "
                    "them would be silently dropped."
                ),
                field=field,
                details={"parameter": key, "values": [existing, value]},
            )

    # ------------------------------------------------------------- walking
    def and_group(self, group: FilterGroup, path: str) -> None:
        """Compile the children of an AND node into params + custom atoms."""
        for index, child in enumerate(group.children):
            self.node_under_and(child, f"{path}.children[{index}]")

    def node_under_and(
        self, node: FilterCondition | FilterGroup | FilterNot, path: str
    ) -> None:
        if isinstance(node, FilterNot):
            self.refuse_negation(path)
            return

        if isinstance(node, FilterCondition):
            self.condition(node, path)
            return

        if node.operator is GroupOperator.AND:
            # AND is associative: a nested AND is the same as its children
            # sitting directly in the parent, whatever mix of sources it has.
            self.and_group(node, path)
            return

        # A nested OR. Compilable only if every leaf under it is a custom
        # field, in which case it becomes one custom_field_query subtree.
        sources = _leaf_sources(node)
        if sources == {True}:
            expression = self.custom_subtree(node, path)
            if expression is not None:
                self.custom.append(expression)
            return
        self.refuse_or(node, sources, path)

    def refuse_or(self, node: FilterGroup, sources: set[bool], path: str) -> None:
        if sources == {False}:
            self.refuse(
                FilterIssueCode.CORE_OR_UNSUPPORTED,
                path,
                (
                    "Paperless cannot express OR between core document fields: "
                    "its filter parameters always narrow the result set, never "
                    "widen it. Split this into separate filters."
                ),
            )
            return
        self.refuse(
            FilterIssueCode.MIXED_OR_UNSUPPORTED,
            path,
            (
                "Paperless cannot express OR between a core document field and "
                "a custom field: custom-field conditions are evaluated in a "
                "separate expression that can only be intersected with the "
                "rest of the query. An OR containing only custom fields is "
                "supported."
            ),
        )

    def refuse_negation(self, path: str) -> None:
        self.refuse(
            FilterIssueCode.NEGATION_UNSUPPORTED,
            path,
            (
                "NOT is not compiled in this version. Paperless negates a "
                "custom-field condition by counting non-matching field "
                "instances, which also matches documents that do not carry "
                "the field at all - a conflation of 'different value' and "
                "'no value' that this engine refuses to make silently. Use "
                "'is missing', 'is not' or 'has none of' instead."
            ),
        )

    def condition(self, condition: FilterCondition, path: str) -> None:
        try:
            resolved = self._catalog.resolve(condition.field)
        except UnknownFieldError:  # pragma: no cover - validation runs first
            self.refuse(
                FilterIssueCode.PARAMETER_CONFLICT,
                path,
                f"{condition.field.key} is not a known field.",
                field=condition.field.key,
            )
            return

        if resolved.is_custom:
            self.custom.append(_custom_atom(condition, resolved))
            return
        self.merge(_compile_core_condition(condition, resolved), path, resolved.key)

    def custom_subtree(
        self, node: FilterCondition | FilterGroup | FilterNot, path: str
    ) -> Any:
        """Compile a custom-field-only subtree into a nested expression."""
        if isinstance(node, FilterNot):
            self.refuse_negation(path)
            return None
        if isinstance(node, FilterCondition):
            return _custom_atom(node, self._catalog.resolve(node.field))

        operands: list[Any] = []
        for index, child in enumerate(node.children):
            compiled = self.custom_subtree(child, f"{path}.children[{index}]")
            if compiled is None:
                return None
            operands.append(compiled)
        return ["AND" if node.operator is GroupOperator.AND else "OR", operands]


def compile_filterset(filterset: FilterSet, catalog: FieldCatalog) -> PaperlessQuery:
    """Compile ``filterset`` to Paperless query parameters.

    Raises :class:`~paperwrench.filters.issues.FilterNotCompilable` - with
    every reason it found, not just the first - if any part of the expression
    has no exact server-side form. **No HTTP request is made either way**;
    this function is pure.
    """
    root = _normalise(filterset.root)

    compiler = _Compiler(catalog)

    if isinstance(root, FilterGroup) and not root.children:
        return PaperlessQuery()

    if isinstance(root, FilterGroup) and root.operator is GroupOperator.OR:
        sources = _leaf_sources(root)
        if sources == {True}:
            compiled_root = compiler.custom_subtree(root, "root")
            if compiled_root is not None:
                compiler.custom.append(compiled_root)
        else:
            compiler.refuse_or(root, sources, "root")
    else:
        # A single condition, a NOT, or an AND group: all handled by the
        # AND path, since a lone node is an AND of one.
        compiler.node_under_and(root, "root")

    expression: Any = None
    if compiler.custom:
        expression = compiler.custom[0] if len(compiler.custom) == 1 else ["AND", compiler.custom]
        depth = _expression_depth(expression)
        atoms = _expression_atoms(expression)
        if depth > CUSTOM_FIELD_QUERY_MAX_DEPTH or atoms > CUSTOM_FIELD_QUERY_MAX_ATOMS:
            compiler.refuse(
                FilterIssueCode.CUSTOM_FIELD_QUERY_TOO_COMPLEX,
                "root",
                (
                    "The custom-field part of this filter exceeds what Paperless "
                    f"accepts (depth {depth}/{CUSTOM_FIELD_QUERY_MAX_DEPTH}, "
                    f"{atoms}/{CUSTOM_FIELD_QUERY_MAX_ATOMS} conditions)."
                ),
                details={
                    "depth": depth,
                    "max_depth": CUSTOM_FIELD_QUERY_MAX_DEPTH,
                    "atoms": atoms,
                    "max_atoms": CUSTOM_FIELD_QUERY_MAX_ATOMS,
                },
            )

    if compiler.issues:
        raise FilterNotCompilable(compiler.issues)

    params = dict(compiler.params)
    if expression is not None:
        # Compact, key-sorted JSON so the same filter always produces the
        # same parameter string - it is recorded and compared, not just sent.
        params["custom_field_query"] = json.dumps(
            expression, separators=(",", ":"), ensure_ascii=False
        )

    return PaperlessQuery(
        params={key: params[key] for key in sorted(params)},
        custom_field_expression=expression,
    )


__all__ = [
    "CUSTOM_FIELD_QUERY_MAX_ATOMS",
    "CUSTOM_FIELD_QUERY_MAX_DEPTH",
    "PaperlessQuery",
    "compile_filterset",
]
