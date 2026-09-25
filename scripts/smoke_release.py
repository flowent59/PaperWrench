"""Probe an installed release from inside its non-root Docker container."""

from __future__ import annotations

import os
import re

import httpx


def main() -> None:
    with httpx.Client(base_url="http://127.0.0.1:8000") as client:
        health = client.get("/api/v1/system/health")
        assert health.json()["version"] == "0.1.0"
        for path in ("/", "/documents/42", "/jobs/42", "/history", "/schemas", "/quality"):
            response = client.get(path)
            assert response.status_code == 200
            assert '<div id="root">' in response.text
            assets = re.findall(r'(?:src|href)="(/assets/[^\"]+)"', response.text)
            assert len(assets) >= 2, "Nested SPA routes must use origin-root asset URLs"
            for asset in assets:
                resource = client.get(asset)
                assert resource.status_code == 200
                assert "text/html" not in resource.headers["content-type"]
        missing = client.get("/api/v1/missing")
        assert missing.status_code == 404 and missing.json()["error"]["code"] == "NOT_FOUND"
        rejected = client.post("/api/v1/collections", json={}, headers={"Origin": "https://evil.test"})
        assert rejected.status_code == 403
        for path in ("/api/v1/system/health", "/api/v1/system/info", "/api/v1/system/paperless"):
            response = client.get(path)
            assert response.status_code == 200
            assert os.environ["PAPERLESS_TOKEN"] not in response.text
    print("Installed wheel: migrations, nested SPA routes/assets and origin guard OK")


if __name__ == "__main__":
    main()
