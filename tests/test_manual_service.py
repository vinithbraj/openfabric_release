from __future__ import annotations

import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from manual.app import create_app
from manual.documents import (
    ManualDocument,
    extract_headings,
    parse_frontmatter,
    render_markdown,
)
from manual.search import ManualSearch, fallback_score
from manual.source_catalog import should_exclude_path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_frontmatter_heading_extraction_and_markdown_rendering() -> None:
    raw = """---
id: sample-doc
title: Sample Doc
kind: manual
tags: [alpha, beta]
order: 4
---
# Sample Doc

Intro text.

## Details

More text.

## Details

```bash
# Not A Heading
```
"""
    metadata, body = parse_frontmatter(raw)

    assert metadata["id"] == "sample-doc"
    assert metadata["tags"] == ["alpha", "beta"]
    assert metadata["order"] == 4

    headings = extract_headings(body)
    assert [heading.id for heading in headings] == ["sample-doc", "details", "details-2"]

    rendered = render_markdown(body)
    assert 'id="sample-doc"' in rendered
    assert 'id="details"' in rendered


def test_app_factory_health_catalog_document_search_and_tags() -> None:
    client = TestClient(create_app(repo_root=REPO_ROOT))

    health = client.get("/healthz")
    assert health.status_code == 200
    assert health.json()["mode"] == "manual"
    assert health.json()["documents"] > 10

    manual_page = client.get("/manual")
    assert manual_page.status_code == 200
    assert "OpenFabric Manual" in manual_page.text
    assert 'localStorage.getItem("openfabric.agentUi.theme")' in manual_page.text
    assert 'localStorage.getItem("openfabric.agentUi.settings")' in manual_page.text
    assert '"dracula"' in manual_page.text
    assert '"gruvbox-dark"' in manual_page.text

    manual_script = client.get("/manual/static/app.js")
    assert manual_script.status_code == 200
    assert 'const settingsStorageKey = "openfabric.agentUi.settings"' in manual_script.text
    assert "function storedThemePreference" in manual_script.text
    assert "function writeThemePreference" in manual_script.text

    catalog = client.get("/api/manual/catalog").json()
    kinds = {group["kind"] for group in catalog["groups"]}
    assert {"manual", "architecture", "generated", "legacy"}.issubset(kinds)

    document = client.get("/api/manual/document/manual-home")
    assert document.status_code == 200
    document_payload = document.json()
    assert document_payload["id"] == "manual-home"
    assert "<h1" in document_payload["html"]
    assert document_payload["reading_time_minutes"] >= 1

    search = client.get("/api/manual/search", params={"q": "gateway", "limit": 5})
    assert search.status_code == 200
    assert search.json()["results"]

    filtered = client.get("/api/manual/search", params={"tags": "gateway", "kind": "manual"})
    assert filtered.status_code == 200
    assert all("gateway" in item["tags"] and item["kind"] == "manual" for item in filtered.json()["results"])

    tags = client.get("/api/manual/tags")
    assert tags.status_code == 200
    assert any(item["tag"] == "manual" for item in tags.json()["tags"])
    assert any(item["tag"] == "context-json" for item in tags.json()["tags"])
    assert any(item["tag"] == "integration" for item in tags.json()["tags"])
    assert any(item["tag"] == "external-systems" for item in tags.json()["tags"])

    parameter_doc = client.get("/api/manual/document/parameter-store-context-sql")
    assert parameter_doc.status_code == 200
    parameter_payload = parameter_doc.json()
    assert "parameter-store" in parameter_payload["tags"]
    assert "metadata" in parameter_payload["tags"]
    assert "doctors" in parameter_payload["tags"]
    assert "Adding Parameter Metadata For The Agent" in parameter_payload["html"]
    assert "Complete Canonical Example" in parameter_payload["html"]
    assert "Concepts JSON" in parameter_payload["html"]

    parameter_search = client.get(
        "/api/manual/search",
        params={"q": "canonical DICOM context_json concepts", "limit": 5},
    )
    assert parameter_search.status_code == 200
    assert any(item["id"] == "parameter-store-context-sql" for item in parameter_search.json()["results"])

    ui_tour = client.get("/api/manual/document/ui-pages-tour")
    assert ui_tour.status_code == 200
    ui_tour_payload = ui_tour.json()
    assert ui_tour_payload["id"] == "ui-pages-tour"
    assert "UI Pages Tour" in ui_tour_payload["html"]
    for route in (
        "/agent-ui",
        "/agent-ui-client",
        "/agent-ui-mobile",
        "/directory",
        "/settings",
        "/prompt-editor",
        "/parameter-editor",
        "/learning-ledger",
        "/reliability",
    ):
        assert route in ui_tour_payload["html"]

    ui_tour_search = client.get(
        "/api/manual/search",
        params={"q": "UI pages tour", "limit": 10},
    )
    assert ui_tour_search.status_code == 200
    assert any(item["id"] == "ui-pages-tour" for item in ui_tour_search.json()["results"])

    install_doc = client.get("/api/manual/document/install-startup")
    assert install_doc.status_code == 200
    install_payload = install_doc.json()
    assert install_payload["id"] == "install-startup"
    assert "Complete Install And Startup Manual" in install_payload["html"]
    assert "./docker-all-services/build.sh" in install_payload["html"]
    assert "./docker-all-services/start.sh" in install_payload["html"]
    assert "GATEWAY_NODE_NAME=workstation" in install_payload["html"]
    assert "uv pip install vllm --torch-backend=auto" in install_payload["html"]
    assert 'class="mermaid"' in install_payload["html"]

    integration_doc = client.get("/api/manual/document/integration-execution-api")
    assert integration_doc.status_code == 200
    integration_payload = integration_doc.json()
    assert integration_payload["id"] == "integration-execution-api"
    assert "integration" in integration_payload["tags"]
    assert "external-systems" in integration_payload["tags"]
    assert "POST /api/agent/integrations/execute" in integration_payload["html"]
    assert "OpenAPI" in integration_payload["html"]

    integration_search = client.get(
        "/api/manual/search",
        params={"q": "integration execute OpenAPI external systems", "limit": 10},
    )
    assert integration_search.status_code == 200
    assert any(
        item["id"] == "integration-execution-api"
        for item in integration_search.json()["results"]
    )

    integration_filtered = client.get(
        "/api/manual/search",
        params={"tags": "integration", "kind": "manual", "limit": 10},
    )
    assert integration_filtered.status_code == 200
    assert any(
        item["id"] == "integration-execution-api"
        for item in integration_filtered.json()["results"]
    )

    route_inventory = client.get("/api/manual/document/generated-route-inventory")
    assert route_inventory.status_code == 200
    assert "/api/agent/integrations/execute" in route_inventory.json()["html"]

    related = client.get("/api/manual/related/manual-home")
    assert related.status_code == 200
    assert related.json()["results"]


def test_document_id_path_traversal_rejection() -> None:
    client = TestClient(create_app(repo_root=REPO_ROOT, include_generated=False))

    response = client.get("/api/manual/document/%2E%2E%2Fsecret")
    assert response.status_code == 400

    response = client.get("/api/manual/related/manual-home%2Fextra")
    assert response.status_code == 400


def test_search_fts_and_python_fallback_scoring() -> None:
    docs = [
        ManualDocument(
            id="alpha",
            title="Gateway Boundaries",
            kind="manual",
            tags=("gateway", "safety"),
            summary="Gateway execution and shell safety.",
            body_markdown="The gateway runs commands after approval.",
            source_type="test",
        ),
        ManualDocument(
            id="beta",
            title="Audio Runtime",
            kind="manual",
            tags=("audio",),
            summary="Transcription service.",
            body_markdown="Voice input and whisper transcription.",
            source_type="test",
        ),
    ]

    assert fallback_score("gateway safety", docs[0]) > fallback_score("gateway safety", docs[1])

    fallback = ManualSearch(docs, use_fts=False)
    assert fallback.search("gateway")[0]["id"] == "alpha"

    fts = ManualSearch(docs, use_fts=True)
    assert fts.search("transcription")[0]["id"] == "beta"


def test_generated_source_catalog_excludes_caches_artifacts_and_binaries() -> None:
    assert should_exclude_path(REPO_ROOT / "src/gateway_agent/.venv/pyvenv.cfg")
    assert should_exclude_path(REPO_ROOT / "artifacts/agent_memory.db")
    assert should_exclude_path(REPO_ROOT / "src/agent_runtime/__pycache__/x.pyc")
    assert should_exclude_path(REPO_ROOT / "vendor/audio-transcriber/models/model.bin")
    assert not should_exclude_path(REPO_ROOT / "src/agent_runtime/api/app.py")


def test_manual_script_syntax() -> None:
    result = subprocess.run(
        ["bash", "-n", "setup-manual.sh", "startmanual.sh"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
