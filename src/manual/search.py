"""Local manual search backed by SQLite FTS5 with a Python fallback."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import Any

from manual.documents import ManualDocument


TOKEN_RE = re.compile(r"[A-Za-z0-9_]{2,}")


@dataclass(frozen=True)
class SearchResult:
    document: ManualDocument
    score: float

    def to_dict(self) -> dict[str, Any]:
        payload = self.document.metadata()
        payload["score"] = round(self.score, 4)
        return payload


def tokenize(value: str) -> list[str]:
    return [match.group(0).lower() for match in TOKEN_RE.finditer(str(value or ""))]


def fallback_score(query: str, document: ManualDocument) -> float:
    """Score a document without SQLite FTS."""

    query_tokens = tokenize(query)
    if not query_tokens:
        return 1.0

    title = document.title.lower()
    tags = " ".join(document.tags).lower()
    summary = document.summary.lower()
    body = document.body_markdown.lower()
    score = 0.0
    for token in query_tokens:
        if token in title:
            score += 8.0
        if token in tags:
            score += 5.0
        if token in summary:
            score += 3.0
        body_count = body.count(token)
        if body_count:
            score += min(body_count, 8) * 0.75
    return score


def _normalize_tags(tags: str | list[str] | tuple[str, ...] | None) -> set[str]:
    if tags is None:
        return set()
    if isinstance(tags, str):
        raw = re.split(r"[, ]+", tags)
    else:
        raw = list(tags)
    return {item.strip().lower() for item in raw if str(item).strip()}


def _matches_filters(document: ManualDocument, *, tags: set[str], kind: str | None) -> bool:
    if kind and document.kind != kind:
        return False
    if tags and not tags.issubset(set(document.tags)):
        return False
    return True


class ManualSearch:
    """Search index for manual documents."""

    def __init__(self, documents: list[ManualDocument], *, use_fts: bool = True) -> None:
        self.documents = list(documents)
        self.document_map = {doc.id: doc for doc in self.documents}
        self.connection: sqlite3.Connection | None = None
        self.fts_available = False
        if use_fts:
            self._build_fts()

    def _build_fts(self) -> None:
        try:
            connection = sqlite3.connect(":memory:")
            connection.execute(
                "CREATE VIRTUAL TABLE manual_fts USING fts5("
                "doc_id UNINDEXED, title, summary, body, tags, kind)"
            )
            connection.executemany(
                "INSERT INTO manual_fts(doc_id, title, summary, body, tags, kind) VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (
                        doc.id,
                        doc.title,
                        doc.summary,
                        doc.body_markdown,
                        " ".join(doc.tags),
                        doc.kind,
                    )
                    for doc in self.documents
                ],
            )
            self.connection = connection
            self.fts_available = True
        except sqlite3.Error:
            self.connection = None
            self.fts_available = False

    def _fts_query(self, query: str) -> str:
        tokens = tokenize(query)
        return " OR ".join(tokens[:12])

    def _search_with_fts(
        self,
        query: str,
        *,
        tags: set[str],
        kind: str | None,
        limit: int,
    ) -> list[SearchResult]:
        if not self.connection:
            return []
        fts_query = self._fts_query(query)
        if not fts_query:
            return []
        try:
            rows = self.connection.execute(
                "SELECT doc_id, bm25(manual_fts) AS rank FROM manual_fts "
                "WHERE manual_fts MATCH ? ORDER BY rank LIMIT ?",
                (fts_query, max(limit * 4, limit)),
            ).fetchall()
        except sqlite3.Error:
            return []

        results: list[SearchResult] = []
        for doc_id, rank in rows:
            document = self.document_map.get(str(doc_id))
            if document is None or not _matches_filters(document, tags=tags, kind=kind):
                continue
            lexical_score = fallback_score(query, document)
            rank_score = 1.0 / (1.0 + abs(float(rank or 0.0)))
            results.append(SearchResult(document=document, score=lexical_score + rank_score))
        return sorted(results, key=lambda item: (-item.score, item.document.order, item.document.title.lower()))[:limit]

    def _search_with_fallback(
        self,
        query: str,
        *,
        tags: set[str],
        kind: str | None,
        limit: int,
    ) -> list[SearchResult]:
        results: list[SearchResult] = []
        for document in self.documents:
            if not _matches_filters(document, tags=tags, kind=kind):
                continue
            score = fallback_score(query, document)
            if query and score <= 0:
                continue
            results.append(SearchResult(document=document, score=score))
        return sorted(results, key=lambda item: (-item.score, item.document.order, item.document.title.lower()))[:limit]

    def search(
        self,
        query: str = "",
        *,
        tags: str | list[str] | tuple[str, ...] | None = None,
        kind: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        normalized_query = str(query or "").strip()
        normalized_tags = _normalize_tags(tags)
        normalized_kind = str(kind or "").strip() or None
        safe_limit = max(1, min(int(limit), 50))

        if normalized_query and self.fts_available:
            results = self._search_with_fts(
                normalized_query,
                tags=normalized_tags,
                kind=normalized_kind,
                limit=safe_limit,
            )
            if results:
                return [result.to_dict() for result in results]

        return [
            result.to_dict()
            for result in self._search_with_fallback(
                normalized_query,
                tags=normalized_tags,
                kind=normalized_kind,
                limit=safe_limit,
            )
        ]

