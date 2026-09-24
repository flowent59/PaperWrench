"""Read-only M6 authoring and one-document evaluation API."""

from fastapi import APIRouter
from fastapi import Depends
from fastapi import Path
from pydantic import BaseModel

from paperwrench.api.deps import get_metadata_registry
from paperwrench.api.deps import get_paperless_client
from paperwrench.paperless import MetadataRegistry
from paperwrench.paperless import PaperlessClient
from paperwrench.transformations import Transformation
from paperwrench.transformations import evaluate
from paperwrench.transformations import validate
from paperwrench.transformations.model import EvaluationResult
from paperwrench.transformations.model import TransformationIssue

router = APIRouter(prefix="/transformations", tags=["transformations"])


class ValidationResult(BaseModel):
    valid: bool
    issues: list[TransformationIssue]


@router.post("/validate", response_model=ValidationResult)
async def validate_transformation(
    transformation: Transformation,
    registry: MetadataRegistry = Depends(get_metadata_registry),
) -> ValidationResult:
    """Check metadata-dependent rules without fetching any document."""
    definitions = {field.id: field for field in await registry.all_custom_fields()}
    issues = validate(transformation, definitions)
    return ValidationResult(valid=not issues, issues=issues)


@router.post("/documents/{document_id}/evaluate", response_model=EvaluationResult)
async def evaluate_document(
    transformation: Transformation,
    document_id: int = Path(gt=0),
    client: PaperlessClient = Depends(get_paperless_client),
    registry: MetadataRegistry = Depends(get_metadata_registry),
) -> EvaluationResult:
    """Compute intended values from one GET and one metadata snapshot. No writes."""
    document = await client.get_document(document_id)
    definitions = {field.id: field for field in await registry.all_custom_fields()}
    return evaluate(document, transformation, definitions)
