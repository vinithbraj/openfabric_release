"""FastAPI application factory for the standalone OpenFabric manual service."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from manual import __version__
from manual.documents import (
    DEFAULT_CONTENT_DIR,
    PACKAGE_DIR,
    ManualDocument,
    catalog_payload,
    load_manual_documents,
    render_markdown,
    tag_counts,
    validate_doc_id,
)
from manual.search import ManualSearch, fallback_score


STATIC_DIR = PACKAGE_DIR / "static"
INDEX_HTML = STATIC_DIR / "index.html"


def _safe_doc_id(doc_id: str) -> str:
    try:
        return validate_doc_id(doc_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid document id") from exc


def _related_documents(document: ManualDocument, documents: list[ManualDocument], limit: int = 6) -> list[dict[str, Any]]:
    own_tags = set(document.tags)
    scored: list[tuple[float, ManualDocument]] = []
    for candidate in documents:
        if candidate.id == document.id:
            continue
        tag_overlap = len(own_tags & set(candidate.tags))
        lexical = fallback_score(document.title + " " + document.summary, candidate)
        score = (tag_overlap * 8.0) + lexical
        if score > 0:
            scored.append((score, candidate))
    scored.sort(key=lambda item: (-item[0], item[1].order, item[1].title.lower()))
    return [candidate.metadata() | {"score": round(score, 4)} for score, candidate in scored[:limit]]


def create_app(
    content_dir: str | Path | None = None,
    repo_root: str | Path | None = None,
    *,
    include_generated: bool = True,
) -> FastAPI:
    """Create the manual FastAPI application."""

    resolved_content_dir = Path(content_dir or DEFAULT_CONTENT_DIR)
    resolved_repo_root = Path(repo_root or Path.cwd())
    documents = load_manual_documents(
        resolved_content_dir,
        resolved_repo_root,
        include_generated=include_generated,
    )
    document_map = {doc.id: doc for doc in documents}
    search_index = ManualSearch(documents)

    app = FastAPI(title="OpenFabric Manual", version=__version__)
    app.state.manual_documents = documents
    app.state.manual_document_map = document_map
    app.state.manual_search = search_index
    app.mount("/manual/static", StaticFiles(directory=str(STATIC_DIR)), name="manual-static")

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse("/manual")

    @app.get("/manual", include_in_schema=False)
    @app.get("/manual/", include_in_schema=False)
    def manual_page() -> FileResponse:
        return FileResponse(INDEX_HTML)

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        return {
            "status": "ok",
            "mode": "manual",
            "version": __version__,
            "documents": len(documents),
            "search": "fts5" if search_index.fts_available else "python",
        }

    @app.get("/api/manual/catalog")
    def catalog() -> dict[str, Any]:
        return catalog_payload(documents)

    @app.get("/api/manual/document/{doc_id:path}")
    def document(doc_id: str) -> dict[str, Any]:
        safe_id = _safe_doc_id(doc_id)
        payload = document_map.get(safe_id)
        if payload is None:
            raise HTTPException(status_code=404, detail="Document not found")
        return payload.metadata() | {"html": render_markdown(payload.body_markdown)}

    @app.get("/api/manual/search")
    def search(
        q: str = "",
        tags: str | None = None,
        kind: str | None = None,
        limit: int = Query(20, ge=1, le=50),
    ) -> dict[str, Any]:
        results = search_index.search(q, tags=tags, kind=kind, limit=limit)
        return {
            "query": q,
            "tags": tags or "",
            "kind": kind or "",
            "count": len(results),
            "results": results,
            "engine": "fts5" if search_index.fts_available else "python",
        }

    @app.get("/api/manual/tags")
    def tags() -> dict[str, Any]:
        return {"tags": tag_counts(documents)}

    @app.get("/api/manual/related/{doc_id:path}")
    def related(doc_id: str, limit: int = Query(6, ge=1, le=12)) -> dict[str, Any]:
        safe_id = _safe_doc_id(doc_id)
        payload = document_map.get(safe_id)
        if payload is None:
            raise HTTPException(status_code=404, detail="Document not found")
        results = _related_documents(payload, documents, limit=limit)
        return {"document_id": safe_id, "results": results}

    return app

