"""Wiring between the Metadata Registry and the pure filter engine.

Everything the validator and compiler do is pure and synchronous. This module
is the only part of the Filter Engine that touches I/O, and it does exactly
one thing: turn the Metadata Registry's custom-field snapshot into a
:class:`~paperwrench.filters.catalog.FieldCatalog`.

Keeping that boundary sharp is what lets the compiler be tested exhaustively
without a server, and what makes "a rejected filter issues zero requests to
Paperless" a property of the code rather than a promise.
"""

from __future__ import annotations

from paperwrench.filters.catalog import FieldCatalog
from paperwrench.filters.compiler import PaperlessQuery
from paperwrench.filters.compiler import compile_filterset
from paperwrench.filters.issues import FilterIssue
from paperwrench.filters.issues import FilterNotCompilable
from paperwrench.filters.issues import FilterValidationError
from paperwrench.filters.model import FilterSet
from paperwrench.filters.validation import validate_filterset
from paperwrench.paperless.registry import MetadataRegistry


async def build_catalog(registry: MetadataRegistry) -> FieldCatalog:
    """Snapshot the currently-known custom fields into a catalog.

    Reads through the registry's TTL cache, so a page of the Explorer that
    validates a filter and then lists documents does not refetch the custom
    field definitions twice.
    """
    return FieldCatalog(await registry.all_custom_fields())


def validate_and_compile(filterset: FilterSet, catalog: FieldCatalog) -> PaperlessQuery:
    """Validate, then compile - raising on the first failing stage.

    The two-stage order matters: a structurally invalid filter must be
    reported as such (``VALIDATION_ERROR``) rather than as
    ``FILTER_NOT_COMPILABLE``, because they call for different things from
    the user. Only a filter that is genuinely well-formed can be said to be
    one Paperless cannot express.
    """
    issues: list[FilterIssue] = validate_filterset(filterset, catalog)
    if issues:
        raise FilterValidationError(issues)
    return compile_filterset(filterset, catalog)


__all__ = [
    "FilterNotCompilable",
    "FilterValidationError",
    "build_catalog",
    "validate_and_compile",
]
