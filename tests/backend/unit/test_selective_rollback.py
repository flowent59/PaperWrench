"""Repeated selected rollback preserves unselected and already restored writes."""

from typing import Any

import pytest

from paperwrench.db.models import Job
from paperwrench.db.models import JobOperation
from paperwrench.db.models import JobStatus
from paperwrench.db.models import OperationStatus
from paperwrench.db.session import session_scope
from paperwrench.errors import PaperWrenchError
from paperwrench.jobs.rollback import candidate_page
from paperwrench.jobs.rollback import create_rollback
from paperwrench.jobs.store import job_view
from paperwrench.jobs.store import rollback_page
from tests.backend.unit.test_jobs import Harness
from tests.backend.unit.test_jobs import harness as rollback_harness
from tests.backend.unit.test_previews import doc
from tests.backend.unit.test_previews import spec
from tests.backend.unit.test_rollback import confirmation
from tests.backend.unit.test_rollback import original

harness = rollback_harness


def counts(job_id: int) -> dict[str, int]:
    summary = job_view(job_id).rollback_counts
    assert summary is not None
    return summary.model_dump()


async def selected(h: Harness, job_id: int, ids: list[int] | None) -> Any:
    return await h.previews.create(
        None, h.client, h.registry, rollback_of_job_id=job_id,
        rollback_document_ids=ids,
    )


async def test_two_of_five_then_full_is_idempotent(harness: Harness) -> None:
    harness.documents.update({i: doc(i) for i in range(3, 6)})
    original_id = await original(harness, spec(ids=[1, 2, 3, 4, 5]))
    candidates = candidate_page(original_id, 1, 25, "", "available", None)
    assert candidates.total == 5
    assert [item.document_id for item in candidates.items] == [1, 2, 3, 4, 5]
    assert candidate_page(original_id, 1, 25, "5", "all", None).total == 1
    staged = await selected(harness, original_id, [2, 4])
    assert staged.evaluated == staged.changed == 2
    first = create_rollback(original_id, confirmation(staged))
    harness.requests.clear()
    await harness.run()
    assert job_view(first).status == "completed"
    assert counts(first) == {
        "selected": 2, "restored": 2, "skipped": 0, "conflicted": 0,
    }
    assert [i for i, d in harness.documents.items() if d["title"] == "Ancien"] == [2, 4]
    assert len([r for r in harness.requests if r.method == "PATCH"]) == 2
    assert candidate_page(original_id, 1, 25, "", "available", None).total == 3
    assert candidate_page(original_id, 1, 25, "", "restored", None).total == 2

    staged = await selected(harness, original_id, None)
    assert (staged.evaluated, staged.changed, staged.unchanged) == (5, 3, 2)
    second = create_rollback(original_id, confirmation(staged))
    harness.requests.clear()
    await harness.run()
    assert counts(second) == {
        "selected": 5, "restored": 3, "skipped": 2, "conflicted": 0,
    }
    assert len([r for r in harness.requests if r.method == "PATCH"]) == 3
    history = rollback_page(original_id, 1, 25, None)
    assert [row.id for row in history.items] == [second, first]
    assert job_view(original_id).rollback_job_id == second
    assert candidate_page(original_id, 1, 25, "", "restored", None).total == 5
    assert (await selected(harness, original_id, [2])).changed == 0


async def test_four_of_150_preserves_other_documents(harness: Harness) -> None:
    harness.documents.update({i: doc(i) for i in range(3, 151)})
    original_id = await original(harness, spec(ids=list(range(1, 151))))
    last = candidate_page(original_id, 6, 25, "", "all", None)
    assert last.total == 150 and last.page_count == 6
    assert [item.document_id for item in last.items] == list(range(126, 151))
    selected_ids = [4, 81, 126, 150]
    rollback_id = create_rollback(
        original_id, confirmation(await selected(harness, original_id, selected_ids)),
    )
    harness.requests.clear()
    await harness.run()
    assert counts(rollback_id)["restored"] == 4
    restored_ids = [i for i, state in harness.documents.items() if state["title"] == "Ancien"]
    assert restored_ids == selected_ids
    assert len([r for r in harness.requests if r.method == "PATCH"]) == 4


async def test_selected_conflict_and_repeated_safe_recovery(harness: Harness) -> None:
    harness.documents.update({3: doc(3)})
    original_id = await original(harness, spec(ids=[1, 2, 3]))
    harness.documents[2]["title"] = "external"
    staged = await selected(harness, original_id, [1, 2])
    assert (staged.changed, staged.errors) == (1, 1)
    rollback_id = create_rollback(original_id, confirmation(staged))
    harness.requests.clear()
    await harness.run()
    assert counts(rollback_id) == {
        "selected": 2, "restored": 1, "skipped": 0, "conflicted": 1,
    }
    assert [r.url.path for r in harness.requests if r.method == "PATCH"] == ["/api/documents/1/"]
    assert harness.documents[2]["title"] == "external"
    assert harness.documents[3]["title"] == "New"
    harness.documents[2]["title"] = "New"
    next_preview = await selected(harness, original_id, [1, 2])
    assert (next_preview.changed, next_preview.unchanged) == (1, 1)
    next_id = create_rollback(original_id, confirmation(next_preview))
    harness.requests.clear()
    await harness.run()
    assert [r.url.path for r in harness.requests if r.method == "PATCH"] == ["/api/documents/2/"]
    assert counts(next_id) == {
        "selected": 2, "restored": 1, "skipped": 1, "conflicted": 0,
    }


async def test_confirmation_rechecks_prior_restoration_and_pending_job(harness: Harness) -> None:
    original_id = await original(harness)
    first = await selected(harness, original_id, [1])
    stale_preview = await selected(harness, original_id, [1])
    rollback_id = create_rollback(original_id, confirmation(first))
    with pytest.raises(PaperWrenchError):
        create_rollback(original_id, confirmation(stale_preview))
    await harness.run()
    with pytest.raises(PaperWrenchError):
        create_rollback(original_id, confirmation(stale_preview))
    assert job_view(rollback_id).status == "completed"


async def test_unknown_prior_result_requires_manual_review(harness: Harness) -> None:
    original_id = await original(harness)
    first = create_rollback(original_id, confirmation(await selected(harness, original_id, [1])))
    with session_scope() as db:
        operation = db.query(JobOperation).filter(JobOperation.job_id == first).one()
        operation.status = OperationStatus.AMBIGUOUS
        job = db.get(Job, first)
        assert job is not None
        job.status = JobStatus.PARTIAL
    staged = await selected(harness, original_id, [1])
    assert staged.changed == 0 and staged.errors == 1
    assert candidate_page(original_id, 1, 25, "", "manual_review", None).total == 1


async def test_reject_foreign_or_duplicate_selection(harness: Harness) -> None:
    original_id = await original(harness)
    with pytest.raises(PaperWrenchError):
        await selected(harness, original_id, [999])
