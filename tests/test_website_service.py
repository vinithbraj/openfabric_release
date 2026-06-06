from __future__ import annotations

import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from website.app import create_app


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_website_app_serves_placeholder_home_subpages_and_health(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("AOR_MANUAL_PORT", "8313")
    monkeypatch.setenv("AOR_PORT", "8311")
    client = TestClient(create_app())

    root = client.get("/", follow_redirects=False)
    assert root.status_code == 307
    assert root.headers["location"] == "/website"

    health = client.get("/healthz")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert health.json()["mode"] == "website"
    assert health.json()["version"]
    assert "runtime_version" in health.json()["runtime"]

    home = client.get("/website")
    assert home.status_code == 200
    assert "Thanks for using openfabric" in home.text
    assert "workspace-preview.png" not in home.text
    assert "agent-product-shots" not in home.text
    assert "__MANUAL_PORT__" not in home.text

    for path in ("/website/product", "/website/use-cases", "/website/resources"):
        response = client.get(path)
        assert response.status_code == 200
        assert "Thanks for using openfabric" in response.text


def test_website_script_syntax() -> None:
    for script in ("startwebsite.sh", "startup.sh"):
        result = subprocess.run(
            ["bash", "-n", script],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
