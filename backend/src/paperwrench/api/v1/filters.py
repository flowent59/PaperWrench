"""The Filter Engine's own API surface.

Three endpoints, each answering one question the frontend cannot answer for
itself without duplicating the compiler:

``GET  /api/v1/filters/capabilities``
    What can be filtered on, and with which operators? Served so the Filter
    Builder renders the backend's rules instead of reimplementing them.

``POST /api/v1/filters/validate``
    Is this filter well-formed, and can Paperless express it? Returns both
    verdicts separately - a filter can be perfectly valid and still not
    compilable (ADR-0007) - and does **not** raise for either: a builder UI
    calls this while the user types, and wants a report, not an exception.

``POST /api/v1/filters/count``
    How many documents match? Compiles the filter and asks Paperless for its
    own ``count``. It never fetches the matching documents, and there is no
    endpoint here that would.

None of these can modify anything, and only ``count`` makes any request to
Paperless at all - validation and capabilities are answered from the
Metadata Registry's cache and pure in-process logic.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi import Depends
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from paperwrench.api.deps import get_metadata_registry
from paperwrench.api.deps import get_paperless_client
from paperwrench.api.v1.documents import build_query_params
from paperwrench.filters import DatasetPageRequest
from paperwrench.filters import FilterIssue
from paperwrench.filters import FilterNotCompilable
from paperwrench.filters import FilterSet
from paperwrench.filters import FilterValidationError
from paperwrench.filters import SearchSpec
from paperwrench.filters import build_catalog
from paperwrench.filters import compile_filterset
from paperwrench.filters import validate_filterset
from paperwrench.filters.capabilities import FilterCapabilities
from paperwrench.filters.capabilities import build_capabilities
from paperwrench.paperless import MetadataRegistry
from paperwrench.paperless import PaperlessClient
from paperwrench.paperless.models import CustomFieldDataType

router = APIRouter(prefix="/filters", tags=["filters"])


class CompiledQuery(BaseModel):
    """The query a FilterSet compiles to, exposed for explanation and audit.

    Shown in the UI behind a "what will be asked" affordance and stored with
    a job in later milestones (ADR-0007), so a history entry can say exactly
    what was sent rather than re-deriving it later from a filter that may
    have been edited since.
    """

    params: dict[str, str]
    custom_field_expression: Any = None


class FilterValidationRequest(BaseModel):
    """Request body. ``extra="forbid"`` throughout this module on purpose.

    Silently ignoring an unrecognised key is the same failure as Paperless
    silently ignoring an unrecognised filter parameter: the caller believes
    something was applied and nothing was. A typo must be an error here.
    """

    model_config = ConfigDict(extra="forbid")

    filters: FilterSet


class FilterValidationResponse(BaseModel):
    """The two verdicts, deliberately kept apart.

    ``valid`` - the expression is well-formed: the fields exist, the
    operators belong to their field's type, the values have the right shape.

    ``compilable`` - Paperless can be asked this exact question. A valid
    filter may still be uncompilable (an OR across a core and a custom
    field, for instance), and that is not the user having made a mistake;
    it is a limit of the server, and the UI should say so differently.

    ``compilable`` is ``false`` whenever ``valid`` is ``false``: an
    expression that is not well-formed has not been shown to be expressible.
    """

    valid: bool
    compilable: bool
    issues: list[FilterIssue] = Field(default_factory=list)
    #: Present only when ``compilable`` is true.
    compiled: CompiledQuery | None = None


class FilterCountRequest(BaseModel):
    """A dataset to count.

    Carries the search spec as well as the filters, because "how many
    documents match" must mean the same set the Explorer is showing, and the
    Explorer's set is search AND filters. Ordering is irrelevant to a count
    and is deliberately rejected rather than ignored: a caller who sent one
    expected it to matter.
    """

    model_config = ConfigDict(extra="forbid")

    filters: FilterSet | None = None
    search: SearchSpec | None = None


class FilterCountResponse(BaseModel):
    count: int
    compiled: CompiledQuery


@router.get(
    "/capabilities",
    response_model=FilterCapabilities,
    summary="Fields, types, operators and grouping rules the compiler supports",
)
async def get_capabilities(
    registry: MetadataRegistry = Depends(get_metadata_registry),
) -> FilterCapabilities:
    """Describe the filter surface of *this* instance.

    The custom fields in the response come from the live Metadata Registry,
    so a field created in Paperless appears here without a PaperWrench
    change and a deleted one stops being offered. Nothing about the field
    list is hardcoded.
    """
    definitions = await registry.all_custom_fields()
    return build_capabilities(
        await build_catalog(registry),
        select_labels={
            definition.id: {
                str(option["id"]): str(option.get("label", option["id"]))
                for option in definition.select_options
                if isinstance(option, dict) and option.get("id") is not None
            }
            for definition in definitions
            if definition.data_type is CustomFieldDataType.SELECT
        },
    )


@router.post(
    "/validate",
    response_model=FilterValidationResponse,
    summary="Report whether a FilterSet is valid and whether it can be compiled",
)
async def validate_filters(
    request: FilterValidationRequest,
    registry: MetadataRegistry = Depends(get_metadata_registry),
) -> FilterValidationResponse:
    """Validate and try to compile, reporting rather than raising.

    Makes **no** request to Paperless: the field catalogue comes from the
    Metadata Registry's cache and everything after that is pure. A filter
    builder can therefore call this on every keystroke without generating
    upstream traffic.
    """
    catalog = await build_catalog(registry)

    issues: list[FilterIssue] = validate_filterset(request.filters, catalog)
    if issues:
        # Not compilable, and deliberately not *attempted*: compiling an
        # expression whose fields or values are unsound would produce a
        # second wave of confusing issues about a filter the user has to fix
        # anyway.
        return FilterValidationResponse(valid=False, compilable=False, issues=issues)

    try:
        compiled = compile_filterset(request.filters, catalog)
    except FilterNotCompilable as exc:
        return FilterValidationResponse(valid=True, compilable=False, issues=exc.issues)

    return FilterValidationResponse(
        valid=True,
        compilable=True,
        compiled=CompiledQuery(
            params=compiled.params,
            custom_field_expression=compiled.custom_field_expression,
        ),
    )


@router.post(
    "/count",
    response_model=FilterCountResponse,
    summary="How many documents match, asked of Paperless without fetching them",
)
async def count_documents(
    request: FilterCountRequest,
    client: PaperlessClient = Depends(get_paperless_client),
    registry: MetadataRegistry = Depends(get_metadata_registry),
) -> FilterCountResponse:
    """Count the matching documents.

    Unlike :func:`validate_filters`, this raises: a count is used to decide
    something, so an uncompilable filter must be an error
    (``FILTER_NOT_COMPILABLE``, 422) rather than a number that quietly means
    something else. A caller that wants a report instead should call
    ``/validate``.

    The count comes from Paperless's own paginated envelope, obtained with
    exactly the parameters a listing would use, and no document is fetched
    to produce it (:meth:`PaperlessClient.count_documents`).
    """
    params = await build_query_params(
        DatasetPageRequest(filters=request.filters, search=request.search),
        registry=registry,
    )

    count = await client.count_documents(params=params)
    return FilterCountResponse(
        count=count,
        compiled=CompiledQuery(
            params={key: str(value) for key, value in sorted(params.items())}
        ),
    )


__all__ = ["FilterValidationError", "router"]
