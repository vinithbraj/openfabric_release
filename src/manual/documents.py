"""Document loading, metadata extraction, and Markdown rendering for the manual."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_CONTENT_DIR = PACKAGE_DIR / "content"
VALID_DOC_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,119}$")


KIND_LABELS = {
    "manual": "Product Manual",
    "architecture": "Architecture Guide",
    "generated": "Living References",
    "legacy": "Legacy Docs",
}

KIND_ORDER = {
    "manual": 10,
    "architecture": 20,
    "generated": 30,
    "legacy": 40,
}


@dataclass(frozen=True)
class Heading:
    """One extracted Markdown heading."""

    id: str
    title: str
    level: int

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "title": self.title, "level": self.level}


@dataclass(frozen=True)
class ManualDocument:
    """Normalized manual document loaded from content or generated from source."""

    id: str
    title: str
    kind: str
    tags: tuple[str, ...]
    summary: str
    body_markdown: str
    source_type: str
    generated: bool = False
    order: int = 1000
    source_path: str | None = None
    headings: tuple[Heading, ...] = field(default_factory=tuple)
    reading_time_minutes: int = 1

    def metadata(self) -> dict[str, Any]:
        """Return API-safe document metadata without the body."""

        return {
            "id": self.id,
            "title": self.title,
            "kind": self.kind,
            "kind_label": KIND_LABELS.get(self.kind, self.kind.title()),
            "tags": list(self.tags),
            "summary": self.summary,
            "source_type": self.source_type,
            "source_path": self.source_path,
            "generated": self.generated,
            "order": self.order,
            "headings": [heading.to_dict() for heading in self.headings],
            "reading_time_minutes": self.reading_time_minutes,
        }

    def searchable_text(self) -> str:
        return " ".join(
            [
                self.title,
                self.summary,
                " ".join(self.tags),
                self.kind,
                self.body_markdown,
            ]
        )


def slugify(value: str, *, fallback: str = "section") -> str:
    """Return a stable lower-case slug."""

    normalized = re.sub(r"[^a-zA-Z0-9\s_-]+", "", str(value or "").strip().lower())
    normalized = re.sub(r"[\s_-]+", "-", normalized).strip("-")
    return normalized or fallback


def validate_doc_id(value: str) -> str:
    """Normalize and reject unsafe document ids."""

    doc_id = str(value or "").strip()
    if not VALID_DOC_ID_RE.fullmatch(doc_id):
        raise ValueError("invalid document id")
    return doc_id


def _parse_scalar(value: str) -> Any:
    value = value.strip()
    if not value:
        return ""
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(item) for item in inner.split(",")]
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if re.fullmatch(r"-?\d+", value):
        try:
            return int(value)
        except ValueError:
            return value
    return value


def parse_frontmatter(raw: str) -> tuple[dict[str, Any], str]:
    """Parse a small YAML-compatible frontmatter block.

    The project already depends on PyYAML, but this parser keeps manual content
    loading lightweight and predictable for simple metadata keys.
    """

    text = raw.replace("\r\n", "\n")
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, text

    end_index: int | None = None
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end_index = index
            break
    if end_index is None:
        return {}, text

    metadata: dict[str, Any] = {}
    current_key: str | None = None
    for line in lines[1:end_index]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- ") and current_key:
            current_value = metadata.setdefault(current_key, [])
            if not isinstance(current_value, list):
                current_value = [current_value]
                metadata[current_key] = current_value
            current_value.append(_parse_scalar(stripped[2:]))
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        current_key = slugify(key, fallback=key.strip()).replace("-", "_")
        parsed_value = _parse_scalar(value)
        metadata[current_key] = parsed_value

    body = "\n".join(lines[end_index + 1 :]).lstrip("\n")
    return metadata, body


def _strip_inline_markdown(value: str) -> str:
    text = re.sub(r"`([^`]+)`", r"\1", value)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"[*_~#]+", "", text)
    return text.strip()


def extract_headings(markdown: str) -> tuple[Heading, ...]:
    """Extract ATX headings from Markdown, ignoring fenced code blocks."""

    headings: list[Heading] = []
    slug_counts: dict[str, int] = {}
    in_fence = False
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if not match:
            continue
        title = _strip_inline_markdown(match.group(2))
        base = slugify(title)
        count = slug_counts.get(base, 0)
        slug_counts[base] = count + 1
        heading_id = base if count == 0 else f"{base}-{count + 1}"
        headings.append(Heading(id=heading_id, title=title, level=len(match.group(1))))
    return tuple(headings)


def _word_count(markdown: str) -> int:
    return len(re.findall(r"[A-Za-z0-9_]+", markdown))


def reading_time_minutes(markdown: str) -> int:
    return max(1, round(_word_count(markdown) / 220))


def infer_summary(markdown: str, *, limit: int = 190) -> str:
    """Use the first prose paragraph as a summary."""

    paragraphs: list[str] = []
    current: list[str] = []
    in_fence = False
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if not stripped:
            if current:
                paragraphs.append(" ".join(current))
                current = []
            continue
        if stripped.startswith("#") or stripped.startswith("|") or re.match(r"^[-*]\s+", stripped):
            continue
        current.append(_strip_inline_markdown(stripped))
    if current:
        paragraphs.append(" ".join(current))
    summary = next((item for item in paragraphs if item), "")
    if len(summary) <= limit:
        return summary
    return summary[: limit - 1].rsplit(" ", 1)[0].rstrip(".,;:") + "."


def _clean_tags(value: Any, *, defaults: tuple[str, ...] = ()) -> tuple[str, ...]:
    if value is None:
        raw_items: list[Any] = list(defaults)
    elif isinstance(value, str):
        raw_items = [item.strip() for item in value.split(",")]
    elif isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    else:
        raw_items = [value]
    tags: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        tag = slugify(str(item), fallback="")
        if tag and tag not in seen:
            seen.add(tag)
            tags.append(tag)
    return tuple(tags)


def _infer_kind(rel_path: Path, metadata: dict[str, Any]) -> str:
    explicit = slugify(str(metadata.get("kind") or ""), fallback="")
    if explicit:
        return explicit
    first_part = rel_path.parts[0] if len(rel_path.parts) > 1 else ""
    if first_part in {"architecture", "legacy"}:
        return first_part
    return "manual"


def _document_from_markdown(path: Path, content_dir: Path) -> ManualDocument:
    raw = path.read_text(encoding="utf-8")
    metadata, body = parse_frontmatter(raw)
    rel_path = path.relative_to(content_dir)
    default_id = slugify(" ".join(rel_path.with_suffix("").parts), fallback=path.stem)
    doc_id = slugify(str(metadata.get("id") or default_id), fallback=default_id)
    kind = _infer_kind(rel_path, metadata)
    source_type = str(metadata.get("source_type") or ("legacy" if kind == "legacy" else "authored"))
    generated = bool(metadata.get("generated", False))
    headings = extract_headings(body)
    title = str(metadata.get("title") or (headings[0].title if headings else path.stem.replace("_", " ").title()))
    tags = _clean_tags(metadata.get("tags"), defaults=(kind,))
    summary = str(metadata.get("summary") or infer_summary(body))
    order = int(metadata.get("order") or 1000)
    return ManualDocument(
        id=validate_doc_id(doc_id),
        title=title,
        kind=kind,
        tags=tags,
        summary=summary,
        body_markdown=body,
        source_type=source_type,
        generated=generated,
        order=order,
        source_path=str(rel_path),
        headings=headings,
        reading_time_minutes=reading_time_minutes(body),
    )


def load_static_documents(content_dir: Path | None = None) -> list[ManualDocument]:
    """Load authored and copied Markdown documents from the manual content tree."""

    root = Path(content_dir or DEFAULT_CONTENT_DIR)
    if not root.exists():
        return []
    documents = [_document_from_markdown(path, root) for path in sorted(root.rglob("*.md"))]
    return sorted(documents, key=lambda doc: (KIND_ORDER.get(doc.kind, 90), doc.order, doc.title.lower()))


def load_manual_documents(
    content_dir: Path | None = None,
    repo_root: Path | None = None,
    *,
    include_generated: bool = True,
) -> list[ManualDocument]:
    """Load static manual content and generated living references."""

    documents = load_static_documents(content_dir)
    if include_generated:
        from manual.source_catalog import generate_reference_pages

        for page in generate_reference_pages(Path(repo_root or Path.cwd())):
            headings = extract_headings(page.body_markdown)
            documents.append(
                ManualDocument(
                    id=validate_doc_id(page.id),
                    title=page.title,
                    kind="generated",
                    tags=_clean_tags(page.tags, defaults=("generated",)),
                    summary=page.summary,
                    body_markdown=page.body_markdown,
                    source_type="generated",
                    generated=True,
                    order=page.order,
                    source_path=page.source_path,
                    headings=headings,
                    reading_time_minutes=reading_time_minutes(page.body_markdown),
                )
            )
    return sorted(documents, key=lambda doc: (KIND_ORDER.get(doc.kind, 90), doc.order, doc.title.lower()))


def tag_counts(documents: list[ManualDocument]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for doc in documents:
        for tag in doc.tags:
            counts[tag] = counts.get(tag, 0) + 1
    return [{"tag": tag, "count": count} for tag, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))]


def catalog_payload(documents: list[ManualDocument]) -> dict[str, Any]:
    """Return grouped catalog metadata for the UI."""

    groups: dict[str, dict[str, Any]] = {}
    for doc in documents:
        group = groups.setdefault(
            doc.kind,
            {
                "kind": doc.kind,
                "label": KIND_LABELS.get(doc.kind, doc.kind.title()),
                "order": KIND_ORDER.get(doc.kind, 90),
                "documents": [],
            },
        )
        group["documents"].append(doc.metadata())
    return {
        "groups": sorted(groups.values(), key=lambda group: (group["order"], group["label"])),
        "documents": [doc.metadata() for doc in documents],
        "tags": tag_counts(documents),
        "total": len(documents),
    }


def _render_inline(text: str) -> str:
    escaped = html.escape(text)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        lambda match: f'<a href="{html.escape(match.group(2), quote=True)}">{match.group(1)}</a>',
        escaped,
    )
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", escaped)
    return escaped


def _render_table(lines: list[str]) -> str:
    rows = [[cell.strip() for cell in line.strip().strip("|").split("|")] for line in lines]
    header = rows[0]
    body_rows = rows[2:]
    html_rows = [
        "<thead><tr>"
        + "".join(f"<th>{_render_inline(cell)}</th>" for cell in header)
        + "</tr></thead>"
    ]
    if body_rows:
        html_rows.append(
            "<tbody>"
            + "".join(
                "<tr>" + "".join(f"<td>{_render_inline(cell)}</td>" for cell in row) + "</tr>"
                for row in body_rows
            )
            + "</tbody>"
        )
    return "<table>" + "".join(html_rows) + "</table>"


def _render_basic_markdown(markdown: str) -> str:
    """Render enough Markdown for the local manual when markdown-it-py is absent."""

    lines = markdown.splitlines()
    html_parts: list[str] = []
    paragraph: list[str] = []
    code_lines: list[str] = []
    code_lang = ""
    in_code = False
    slug_counts: dict[str, int] = {}
    index = 0

    def flush_paragraph() -> None:
        if paragraph:
            html_parts.append("<p>" + _render_inline(" ".join(paragraph)) + "</p>")
            paragraph.clear()

    def heading_id(title: str) -> str:
        base = slugify(title)
        count = slug_counts.get(base, 0)
        slug_counts[base] = count + 1
        return base if count == 0 else f"{base}-{count + 1}"

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if stripped.startswith("```"):
            if in_code:
                code = "\n".join(code_lines)
                if code_lang == "mermaid":
                    html_parts.append(f'<div class="mermaid">{html.escape(code)}</div>')
                else:
                    class_name = f' class="language-{html.escape(code_lang, quote=True)}"' if code_lang else ""
                    html_parts.append(f"<pre><code{class_name}>{html.escape(code)}</code></pre>")
                code_lines = []
                code_lang = ""
                in_code = False
            else:
                flush_paragraph()
                code_lang = slugify(stripped[3:].strip(), fallback="")
                in_code = True
            index += 1
            continue
        if in_code:
            code_lines.append(line)
            index += 1
            continue
        if not stripped:
            flush_paragraph()
            index += 1
            continue
        heading_match = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if heading_match:
            flush_paragraph()
            level = len(heading_match.group(1))
            title = _strip_inline_markdown(heading_match.group(2))
            html_parts.append(
                f'<h{level} id="{heading_id(title)}">{_render_inline(title)}</h{level}>'
            )
            index += 1
            continue
        if re.match(r"^[-*]\s+", stripped):
            flush_paragraph()
            items: list[str] = []
            while index < len(lines):
                item_match = re.match(r"^[-*]\s+(.+)$", lines[index].strip())
                if not item_match:
                    break
                items.append(item_match.group(1))
                index += 1
            html_parts.append("<ul>" + "".join(f"<li>{_render_inline(item)}</li>" for item in items) + "</ul>")
            continue
        if (
            "|" in line
            and index + 1 < len(lines)
            and re.match(r"^\s*\|?[\s:-]+\|[\s|:-]+\s*$", lines[index + 1])
        ):
            flush_paragraph()
            table_lines = [line, lines[index + 1]]
            index += 2
            while index < len(lines) and "|" in lines[index] and lines[index].strip():
                table_lines.append(lines[index])
                index += 1
            html_parts.append(_render_table(table_lines))
            continue
        paragraph.append(stripped)
        index += 1

    flush_paragraph()
    if in_code:
        html_parts.append(f"<pre><code>{html.escape(chr(10).join(code_lines))}</code></pre>")
    return "\n".join(html_parts)


def _add_heading_ids(rendered_html: str, headings: tuple[Heading, ...]) -> str:
    iterator = iter(headings)

    def replace(match: re.Match[str]) -> str:
        try:
            heading = next(iterator)
        except StopIteration:
            return match.group(0)
        level = match.group(1)
        attrs = match.group(2) or ""
        inner = match.group(3)
        if " id=" in attrs:
            return match.group(0)
        return f'<h{level}{attrs} id="{heading.id}">{inner}</h{level}>'

    return re.sub(r"<h([1-6])([^>]*)>(.*?)</h\1>", replace, rendered_html, flags=re.DOTALL)


def _replace_mermaid_codeblocks(rendered_html: str) -> str:
    pattern = re.compile(
        r'<pre><code class="language-mermaid">(.*?)</code></pre>',
        flags=re.DOTALL,
    )
    return pattern.sub(lambda match: f'<div class="mermaid">{match.group(1)}</div>', rendered_html)


def render_markdown(markdown: str) -> str:
    """Render Markdown to HTML for API responses."""

    headings = extract_headings(markdown)
    try:
        from markdown_it import MarkdownIt

        renderer = MarkdownIt("commonmark", {"html": False})
        try:
            renderer.enable("table")
        except Exception:
            pass
        rendered = renderer.render(markdown)
        return _replace_mermaid_codeblocks(_add_heading_ids(rendered, headings))
    except Exception:
        return _render_basic_markdown(markdown)

