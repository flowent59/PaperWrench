"""Test-only disposable subprocess. Never a production entry point."""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

import httpx

from paperwrench.config import Settings
from paperwrench.db.engine import init_engine
from paperwrench.db.lock import acquire_lock
from paperwrench.db.session import session_scope
from paperwrench.jobs.engine import JobEngine
from paperwrench.paperless import MetadataRegistry
from paperwrench.paperless import PaperlessClient
from paperwrench.paperless.models import Document
from tests.backend.live.conftest import MAX_SANDBOX_DOCUMENTS
from tests.backend.live.conftest import assert_authorised_target
from tests.backend.live.conftest import live_tests_allowed


async def run() -> None:
    assert live_tests_allowed(), "Live-test opt-in is required"
    url = os.environ["PW_CRASH_URL"]
    assert_authorised_target(url)
    database_url, raw_job_id, phase = sys.argv[1:]
    settings = Settings(
        PAPERLESS_URL=url,
        PAPERLESS_TOKEN=os.environ["PW_CRASH_TOKEN"],
        PAPERWRENCH_DATABASE_URL=database_url,
    )

    class CrashClient(PaperlessClient):
        patched = False

        async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
            if method == "PATCH" and phase == "before_patch":
                os._exit(73)
            response = await super()._request(method, path, **kwargs)
            if method == "PATCH":
                self.patched = True
                if phase == "after_patch":
                    os._exit(73)
            return response

        async def get_document(self, document_id: int) -> Document:
            result = await super().get_document(document_id)
            if self.patched and phase == "after_readback":
                os._exit(73)
            return result

    async with CrashClient(settings) as client:
        probe = await client.check_connection()
        assert probe.paperless_version == "3.1.2"
        assert probe.document_count is not None and probe.document_count <= MAX_SANDBOX_DOCUMENTS
        init_engine(database_url)
        with session_scope() as session:
            acquire_lock(session, "m8-crash-child", force=True)
        worker = JobEngine(client, MetadataRegistry(client), settings, "m8-crash-child")
        target = worker._claim()
        assert target is not None and target[0] == int(raw_job_id)
        if phase == "before_intent":
            os._exit(73)
        await worker.execute(*target)
        # after_commit: both result and Job aggregation have reached SQLite.
        os._exit(73)


if __name__ == "__main__":
    asyncio.run(run())
