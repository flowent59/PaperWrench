"""Bounded read-only preview orchestration and single-use confirmation.

SQLite stages only M6 result rows, never OCR, complete documents or Jobs.
Short transactions never span an upstream await. The deployment is single-process.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import secrets
from datetime import timedelta
from typing import Any
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import delete
from sqlalchemy import func
from sqlalchemy import select
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from paperwrench.db.base import utcnow
from paperwrench.db.models import JobTarget
from paperwrench.db.models import Preview
from paperwrench.db.models import PreviewDocument
from paperwrench.db.session import session_scope
from paperwrench.errors import ErrorCode
from paperwrench.errors import PaperlessForbiddenError
from paperwrench.errors import PaperWrenchError
from paperwrench.filters.model import CustomFieldRef
from paperwrench.filters.model import DatasetPageRequest
from paperwrench.inspector import catalog_revision
from paperwrench.paperless import MetadataRegistry
from paperwrench.paperless import PaperlessClient
from paperwrench.paperless.errors import PaperlessNotFoundError
from paperwrench.paperless.models import CustomField
from paperwrench.paperless.models import Document
from paperwrench.paperless.models import MetadataKind
from paperwrench.paperless.mutations import revision
from paperwrench.previews.model import MAX_BYTES
from paperwrench.previews.model import MAX_DOCUMENTS
from paperwrench.previews.model import MAX_PREVIEWS
from paperwrench.previews.model import PAGE_SIZE
from paperwrench.previews.model import PREVIEW_VERSION
from paperwrench.previews.model import TIMEOUT_SECONDS
from paperwrench.previews.model import TTL_SECONDS
from paperwrench.previews.model import ConfirmPreview
from paperwrench.previews.model import CreatedPreview
from paperwrench.previews.model import PreviewPage
from paperwrench.previews.model import PreviewRow
from paperwrench.previews.model import PreviewSummary
from paperwrench.transformations import evaluate
from paperwrench.transformations.model import DatasetTargets
from paperwrench.transformations.model import ResultStatus
from paperwrench.transformations.model import Transformation
from paperwrench.transformations.model import TransformationIssue


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def identities(spec: Transformation) -> tuple[str, str]:
    """Reuse M4 identity exactly; explicit IDs are an unordered, sorted set."""
    target = spec.targets
    selection = (
        target.query.fingerprint()
        if isinstance(target, DatasetTargets)
        else fingerprint(sorted(target.document_ids))
    )
    spec_hash = fingerprint(
        {
            "version": PREVIEW_VERSION,
            "source": target.source,
            "selection": selection,
            "operations": [operation.model_dump(mode="json") for operation in spec.operations],
        }
    )
    return selection, spec_hash


def stale(
    message: str = "Preview is unavailable, expired or already confirmed.",
) -> PaperWrenchError:
    return PaperWrenchError(message, status_code=409, code=ErrorCode.PREVIEW_STALE)


def limit(message: str) -> PaperWrenchError:
    return PaperWrenchError(message, status_code=422, code=ErrorCode.VALIDATION_ERROR)


def cleanup(*, startup: bool = False) -> None:
    """Expire staging; abandoned builds are discarded after process restart."""
    with session_scope() as session:
        condition = Preview.expires_at <= utcnow()
        if startup:
            condition = condition | (Preview.ready.is_(False))
        session.execute(delete(Preview).where(condition))


def _row(document: Document, spec: Transformation, fields: dict[int, CustomField]) -> PreviewRow:
    result = evaluate(document, spec, fields)
    issue = None
    if document.user_can_change is not True or document.deleted_at is not None:
        issue = TransformationIssue(
            code="DOCUMENT_NOT_EDITABLE", message="Document is not confirmed editable."
        )
    status = (
        ResultStatus.ERROR
        if issue or any(c.status == ResultStatus.ERROR for c in result.changes)
        else ResultStatus.CHANGE
        if any(c.status == ResultStatus.CHANGE for c in result.changes)
        else ResultStatus.UNCHANGED
    )
    return PreviewRow(
        **result.model_dump(), title=document.title, status=status, issue=issue,
        observed_revision=revision(document),
        catalog_revision=catalog_revision(list(fields.values())),
    )


def _error_row(document_id: int, code: str, message: str) -> PreviewRow:
    return PreviewRow(
        document_id=document_id,
        changes=[],
        status=ResultStatus.ERROR,
        issue=TransformationIssue(code=code, message=message),
    )


class PreviewService:
    """One active builder; four staged previews maximum in the single instance."""

    def __init__(self) -> None:
        self._building = False

    async def create(
        self,
        spec: Transformation | None,
        client: PaperlessClient,
        registry: MetadataRegistry,
        *, rollback_of_job_id: int | None = None,
        owner_id: int | None = None,
    ) -> CreatedPreview:
        if self._building:
            raise PaperWrenchError(
                "A preview is already building; try again later.",
                status_code=409,
                code=ErrorCode.CONFLICT,
            )
        self._building = True
        preview_id = uuid4().hex
        try:
            async with asyncio.timeout(TIMEOUT_SECONDS):
                return await self._build(
                    preview_id, spec, client, registry, rollback_of_job_id, owner_id
                )
        except BaseException as exc:
            with session_scope() as session:
                session.execute(delete(Preview).where(Preview.id == preview_id))
            if isinstance(exc, TimeoutError):
                raise limit(
                    "Preview exceeded the 300 second build limit; narrow the selection."
                ) from exc
            raise
        finally:
            self._building = False

    async def _build(
        self,
        preview_id: str,
        spec: Transformation | None,
        client: PaperlessClient,
        registry: MetadataRegistry,
        rollback_of_job_id: int | None = None,
        owner_id: int | None = None,
    ) -> CreatedPreview:
        # The existing query adapter is re-used verbatim. Import after router
        # assembly to avoid the api.v1 package's eager router imports.
        from paperwrench.api.v1.documents import build_query_params

        cleanup()
        with session_scope() as session:
            if (
                session.scalar(
                    select(func.count())
                    .select_from(Preview)
                    .where(Preview.owner_id == owner_id)
                )
                or 0
            ) >= MAX_PREVIEWS:
                raise limit("Four previews are retained; discard one or wait for expiry.")
        if (spec is not None and spec.targets.source == "ids"
                and len(spec.targets.document_ids) > MAX_DOCUMENTS):
            raise limit("Preview supports at most 100000 explicit IDs.")

        # One metadata snapshot for compiler and evaluator. Compilation must finish
        # before any document GET. Metadata reads may warm the existing TTL cache.
        if rollback_of_job_id is not None:
            from paperwrench.jobs.rollback import require_original

            with session_scope() as session:
                require_original(session, rollback_of_job_id, owner_id)
            await registry.refresh(MetadataKind.CUSTOM_FIELD)
        fields = {field.id: field for field in await registry.all_custom_fields()}
        params: dict[str, Any] = {}
        if spec is not None and isinstance(spec.targets, DatasetTargets):
            params = await build_query_params(
                DatasetPageRequest(**spec.targets.query.model_dump(), page_size=PAGE_SIZE),
                registry=registry,
            )
        if rollback_of_job_id is not None:
            selection = fingerprint({"rollback_of_job_id": rollback_of_job_id})
            spec_hash = fingerprint({"rollback": selection, "version": PREVIEW_VERSION})
        else:
            assert spec is not None
            selection, spec_hash = identities(spec)
        token = secrets.token_urlsafe(32)
        started = utcnow()
        expiry = started + timedelta(seconds=TTL_SECONDS)
        with session_scope() as session:
            session.add(
                Preview(
                    id=preview_id,
                    owner_id=owner_id,
                    expires_at=expiry,
                    token_hash=hashlib.sha256(token.encode()).hexdigest(),
                )
            )

        counts = dict.fromkeys(ResultStatus, 0)
        evaluated = 0
        byte_count = 0
        results_hash = hashlib.sha256()
        requires_race_ack = False

        def stage(rows: list[PreviewRow]) -> None:
            nonlocal evaluated, byte_count, requires_race_ack
            with session_scope() as session:
                for row in rows:
                    if row.rollback_spec and any(
                        isinstance(o.field, CustomFieldRef) for o in row.rollback_spec.operations
                    ):
                        requires_race_ack = True
                    payload = canonical(row.model_dump(mode="json"))
                    byte_count += len(payload)
                    if byte_count > MAX_BYTES:
                        raise limit("Preview exceeds 128 MiB of result data; narrow the selection.")
                    results_hash.update(payload + b"\n")
                    session.add(
                        PreviewDocument(
                            preview_id=preview_id,
                            position=evaluated,
                            document_id=row.document_id,
                            status=row.status,
                            result_json=payload.decode("utf-8"),
                        )
                    )
                    counts[row.status] += 1
                    evaluated += 1
                    if evaluated > MAX_DOCUMENTS:
                        raise limit("Preview exceeds 100000 documents; narrow the selection.")
                try:
                    session.flush()
                except IntegrityError as exc:
                    raise stale("Dataset returned a repeated ID; run a new preview.") from exc

        if rollback_of_job_id is not None:
            from paperwrench.jobs.rollback import preview_row

            position = -1
            while True:
                with session_scope() as session:
                    batch_targets = session.execute(select(
                        JobTarget.document_id, JobTarget.position
                    ).where(
                        JobTarget.job_id == rollback_of_job_id, JobTarget.position > position
                    ).order_by(JobTarget.position).limit(PAGE_SIZE)).all()
                if not batch_targets:
                    break
                stage([
                    await preview_row(
                        rollback_of_job_id,
                        target.document_id,
                        client,
                        fields,
                        owner_id,
                    )
                    for target in batch_targets
                ])
                position = batch_targets[-1].position
            matched = evaluated
        elif spec is not None and spec.targets.source == "ids":
            matched = len(spec.targets.document_ids)
            buffer: list[PreviewRow] = []
            for document_id in sorted(spec.targets.document_ids):
                try:
                    document = await client.get_document(document_id)
                    if document.id != document_id:
                        raise stale("Paperless returned a different document ID.")
                    row = _row(document, spec, fields)
                except PaperlessNotFoundError:
                    row = _error_row(
                        document_id,
                        "DOCUMENT_UNAVAILABLE",
                        "Document is missing or invisible to this credential.",
                    )
                except PaperlessForbiddenError:
                    row = _error_row(
                        document_id, "DOCUMENT_FORBIDDEN", "Document access was refused."
                    )
                except ValidationError:
                    row = _error_row(
                        document_id, "DOCUMENT_INVALID", "Document response is invalid."
                    )
                buffer.append(row)
                if len(buffer) == PAGE_SIZE:
                    stage(buffer)
                    buffer.clear()
            stage(buffer)
        else:
            assert spec is not None
            page = 1
            matched = -1
            while True:
                try:
                    batch = await client.list_documents(
                        params=params, page=page, page_size=PAGE_SIZE
                    )
                except (PaperlessNotFoundError, ValidationError) as exc:
                    raise stale(
                        "Dataset page disappeared or is invalid; run a new preview."
                    ) from exc
                if matched == -1:
                    matched = batch.count
                if matched > MAX_DOCUMENTS:
                    raise limit("Dataset exceeds 100000 documents; narrow the selection.")
                if batch.count != matched or len(batch.results) > PAGE_SIZE:
                    raise stale("Dataset pagination changed while reading; run a new preview.")
                if not batch.results and batch.next:
                    raise stale("Dataset returned an empty intermediate page.")
                stage([_row(document, spec, fields) for document in batch.results])
                if evaluated > matched or (batch.next and evaluated >= matched):
                    raise stale("Dataset pagination is inconsistent; run a new preview.")
                if not batch.next:
                    break
                page += 1
            if evaluated != matched:
                raise stale("Dataset count differs from the staged targets; run a new preview.")

        # Sorted IDs are hashed by a streaming database cursor: no target array.
        targets_hash = hashlib.sha256()
        with session_scope() as session:
            ids = session.scalars(
                select(PreviewDocument.document_id)
                .where(PreviewDocument.preview_id == preview_id)
                .order_by(PreviewDocument.document_id)
                .execution_options(yield_per=PAGE_SIZE)
            )
            for document_id in ids:
                targets_hash.update(f"{document_id}\n".encode())
            summary = PreviewSummary(
                id=preview_id,
                created_at=started,
                expires_at=expiry,
                matched=matched,
                evaluated=evaluated,
                changed=counts[ResultStatus.CHANGE],
                unchanged=counts[ResultStatus.UNCHANGED],
                errors=counts[ResultStatus.ERROR],
                selection_fingerprint=selection,
                spec_fingerprint=spec_hash,
                target_fingerprint=targets_hash.hexdigest(),
                result_fingerprint=results_hash.hexdigest(),
                rollback_of_job_id=rollback_of_job_id,
                requires_external_race_ack=requires_race_ack,
            )
            session.execute(
                update(Preview)
                .where(Preview.id == preview_id)
                .values(
                    ready=True,
                    summary_json=summary.model_dump_json(),
                )
            )
        return CreatedPreview(**summary.model_dump(), preview_token=token)

    def summary(self, preview_id: str, owner_id: int | None = None) -> PreviewSummary:
        with session_scope() as session:
            preview = session.get(Preview, preview_id)
            if (
                preview is None
                or preview.owner_id != owner_id
                or not preview.ready
                or preview.expires_at <= utcnow()
            ):
                raise stale()
            return PreviewSummary.model_validate_json(preview.summary_json).model_copy(
                update={"confirmed": preview.confirmed}
            )

    def page(
        self,
        preview_id: str,
        page: int,
        page_size: int,
        status: ResultStatus | None = None,
        owner_id: int | None = None,
    ) -> PreviewPage:
        from paperwrench.api.v1.documents import validate_page_size

        self.summary(preview_id, owner_id)
        validate_page_size(page_size)
        if page < 1:
            raise limit("Page must be positive.")
        condition = PreviewDocument.preview_id == preview_id
        if status is not None:
            condition = condition & (PreviewDocument.status == status)
        with session_scope() as session:
            total = (
                session.scalar(select(func.count()).select_from(PreviewDocument).where(condition))
                or 0
            )
            payloads = session.scalars(
                select(PreviewDocument.result_json)
                .where(condition)
                .order_by(PreviewDocument.position)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
            return PreviewPage(
                items=[PreviewRow.model_validate_json(payload) for payload in payloads],
                page=page,
                page_size=page_size,
                total=total,
                page_count=(total + page_size - 1) // page_size,
            )

    def confirm(
        self, preview_id: str, request: ConfirmPreview, owner_id: int | None = None
    ) -> PreviewSummary:
        with session_scope() as session:
            return self.claim(session, preview_id, request, owner_id)

    def claim(
        self,
        session: Session,
        preview_id: str,
        request: ConfirmPreview,
        owner_id: int | None = None,
    ) -> PreviewSummary:
        """Consume inside the caller's transaction; never commit here."""
        preview = session.get(Preview, preview_id)
        if (
            preview is None
            or preview.owner_id != owner_id
            or not preview.ready
            or preview.expires_at <= utcnow()
        ):
            raise stale()
        summary = PreviewSummary.model_validate_json(preview.summary_json).model_copy(
            update={"confirmed": preview.confirmed}
        )
        selection, spec_hash = identities(request.transformation)
        if (
            summary.confirmed
            or summary.rollback_of_job_id is not None
            or summary.errors
            or not summary.changed
            or selection != summary.selection_fingerprint
            or spec_hash != summary.spec_fingerprint
            or request.target_fingerprint != summary.target_fingerprint
            or request.result_fingerprint != summary.result_fingerprint
            or request.version != summary.version
        ):
            raise stale("Confirmation does not match an error-free, changed preview.")
        token_hash = hashlib.sha256(request.preview_token.encode()).hexdigest()
        if not hmac.compare_digest(preview.token_hash, token_hash):
            raise stale("Invalid preview token.")
        claimed = session.execute(
            update(Preview)
            .where(
                Preview.id == preview_id,
                Preview.owner_id == owner_id,
                Preview.confirmed.is_(False),
                Preview.ready.is_(True),
                Preview.expires_at > utcnow(),
                Preview.token_hash == token_hash,
            )
            .values(confirmed=True)
            .returning(Preview.id)
        ).scalar_one_or_none()
        if claimed is None:
            raise stale()
        return summary.model_copy(update={"confirmed": True})

    def discard(self, preview_id: str, owner_id: int | None = None) -> None:
        with session_scope() as session:
            session.execute(
                delete(Preview).where(
                    Preview.id == preview_id,
                    Preview.owner_id == owner_id,
                    Preview.ready.is_(True),
                )
            )
