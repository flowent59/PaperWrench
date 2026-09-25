"""One compiled-browser journey; the shared live gates run before this test."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from paperwrench.config import Settings

pytestmark = pytest.mark.live


def test_compiled_mvp_journey(live_settings: Settings, tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[3]
    assert (root / "backend/src/paperwrench/static/index.html").is_file(), "Build SPA first"
    env = os.environ.copy()
    env.update({
        "PAPERLESS_URL": live_settings.paperless_url,
        "PAPERLESS_TOKEN": live_settings.paperless_token.get_secret_value(),
        "PAPERLESS_TOKEN_FILE": "",
        "PAPERWRENCH_DATABASE_URL": f"sqlite+pysqlite:///{tmp_path / 'browser.db'}",
        "PAPERWRENCH_ALLOW_LIVE_TESTS": "true",
    })
    log_path = tmp_path / "server.log"
    with log_path.open("w", encoding="utf-8") as log:
        server = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "paperwrench.main:app", "--host", "127.0.0.1",
             "--port", "8020"], cwd=root, env=env, stdout=log, stderr=log,
        )
        try:
            for _ in range(100):
                assert server.poll() is None, "Acceptance server exited"
                try:
                    response = httpx.get("http://127.0.0.1:8020/api/v1/system/health")
                    if response.status_code == 200:
                        break
                except httpx.ConnectError:
                    pass
                time.sleep(0.1)
            else:
                pytest.fail("Acceptance server did not start")
            result = subprocess.run(
                [shutil.which("node") or "node", "node_modules/@playwright/test/cli.js", "test"],
                cwd=root / "frontend", env=env, capture_output=True, text=True,
                encoding="utf-8", timeout=240,
            )
            output = result.stdout + result.stderr
            assert live_settings.paperless_token.get_secret_value() not in output
            print(output.encode("ascii", errors="backslashreplace").decode("ascii"))
            assert result.returncode == 0
        finally:
            server.terminate()
            server.wait(timeout=20)
    assert live_settings.paperless_token.get_secret_value() not in log_path.read_text("utf-8")
