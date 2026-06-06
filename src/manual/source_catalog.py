"""Generate living reference pages by inspecting source files."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path


SOURCE_ROOTS = (
    "src/agent_runtime",
    "src/audio_runtime",
    "src/gateway_agent",
    "src/gateway_core",
    "src/llm",
)

EXCLUDED_PARTS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "artifacts",
    "build",
    "dist",
    "node_modules",
    "results",
    "vendor",
}

BINARY_SUFFIXES = {
    ".bin",
    ".db",
    ".dll",
    ".dylib",
    ".gif",
    ".jpg",
    ".jpeg",
    ".o",
    ".png",
    ".pyc",
    ".so",
    ".sqlite",
    ".webp",
    ".zip",
}


@dataclass(frozen=True)
class GeneratedPage:
    id: str
    title: str
    summary: str
    body_markdown: str
    tags: tuple[str, ...]
    order: int
    source_path: str | None = None


def should_exclude_path(path: Path) -> bool:
    """Return whether a source path should be skipped by generated references."""

    parts = set(path.parts)
    if parts & EXCLUDED_PARTS:
        return True
    return path.suffix.lower() in BINARY_SUFFIXES


def iter_source_files(
    repo_root: Path,
    *,
    suffixes: tuple[str, ...] = (".py", ".sh"),
) -> list[Path]:
    """Return source files under the supported runtime roots."""

    root = Path(repo_root)
    files: list[Path] = []
    for relative in SOURCE_ROOTS:
        source_root = root / relative
        if not source_root.exists():
            continue
        for path in source_root.rglob("*"):
            if should_exclude_path(path):
                continue
            if path.is_file() and path.suffix in suffixes:
                files.append(path)
    return sorted(files)


def _relative(path: Path, repo_root: Path) -> str:
    try:
        return str(path.relative_to(repo_root))
    except ValueError:
        return str(path)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="ignore")


def _first_docstring_line(tree: ast.AST) -> str:
    docstring = ast.get_docstring(tree) or ""
    return docstring.strip().splitlines()[0] if docstring.strip() else ""


def build_module_catalog(repo_root: Path) -> str:
    rows: list[tuple[str, str, int, int, str]] = []
    for path in iter_source_files(repo_root, suffixes=(".py",)):
        relative = _relative(path, repo_root)
        try:
            tree = ast.parse(_read_text(path))
        except SyntaxError:
            rows.append((relative, "", 0, 0, "Could not parse."))
            continue
        classes = sum(isinstance(node, ast.ClassDef) for node in tree.body)
        functions = sum(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) for node in tree.body)
        module_name = relative.removeprefix("src/").removesuffix(".py").replace("/", ".")
        rows.append((relative, module_name, classes, functions, _first_docstring_line(tree)))

    table_rows = [
        "| File | Module | Classes | Functions | Summary |",
        "| --- | --- | ---: | ---: | --- |",
    ]
    for relative, module_name, classes, functions, summary in rows:
        table_rows.append(f"| `{relative}` | `{module_name}` | {classes} | {functions} | {summary or '-'} |")

    return "\n".join(
        [
            "# Source Module Catalog",
            "",
            "Generated from Python files in the runtime source roots. Caches, artifacts, vendored binaries, and virtual environments are excluded.",
            "",
            "```mermaid",
            "flowchart LR",
            "    A[agent_runtime] --> B[operator]",
            "    A --> C[execution]",
            "    A --> D[api]",
            "    D --> E[Agent UI]",
            "    C --> F[gateway_core]",
            "    A --> G[memory/events/tasks]",
            "    H[audio_runtime] --> D",
            "    I[llm launchers] --> F",
            "```",
            "",
            *table_rows,
        ]
    )


ROUTE_RE = re.compile(
    r"@(?P<target>app|router)\.(?P<method>get|post|put|patch|delete|websocket)\(\s*[\"'](?P<path>[^\"']+)[\"']"
)
DEF_RE = re.compile(r"^\s*(?:async\s+def|def)\s+([A-Za-z_][A-Za-z0-9_]*)")


def build_route_inventory(repo_root: Path) -> str:
    rows: list[tuple[str, str, str, str]] = []
    for path in iter_source_files(repo_root, suffixes=(".py",)):
        lines = _read_text(path).splitlines()
        for index, line in enumerate(lines):
            if not re.search(
                r"@(app|router)\.(get|post|put|patch|delete|websocket)\(",
                line,
            ):
                continue
            decorator = line.strip()
            if not ROUTE_RE.search(decorator):
                for follow in lines[index + 1 : index + 10]:
                    decorator = f"{decorator} {follow.strip()}"
                    if ")" in follow:
                        break
            match = ROUTE_RE.search(decorator)
            if not match:
                continue
            handler = ""
            for follow in lines[index + 1 : index + 8]:
                def_match = DEF_RE.match(follow)
                if def_match:
                    handler = def_match.group(1)
                    break
            rows.append((match.group("method").upper(), match.group("path"), _relative(path, repo_root), handler))

    table_rows = [
        "| Method | Path | Handler | Source |",
        "| --- | --- | --- | --- |",
    ]
    for method, route_path, source, handler in sorted(rows, key=lambda item: (item[1], item[0], item[2])):
        table_rows.append(f"| `{method}` | `{route_path}` | `{handler or '-'}` | `{source}` |")

    return "\n".join(
        [
            "# FastAPI Route Inventory",
            "",
            "Generated from FastAPI route decorators in the runtime, gateway, and audio services.",
            "",
            "```mermaid",
            "flowchart LR",
            "    C[Client] --> A[Agent API]",
            "    A --> U[Agent UI routes]",
            "    A --> R[Runtime endpoints]",
            "    A --> G[Gateway registry]",
            "    G --> X[Gateway Agent]",
            "    A --> T[Audio transcriber]",
            "```",
            "",
            *table_rows,
        ]
    )


ENV_NAME_RE = re.compile(r"[\"']([A-Z][A-Z0-9_]{2,})[\"']")
GETENV_DEFAULT_RE = re.compile(r"os\.getenv\(\s*[\"']([A-Z][A-Z0-9_]{2,})[\"']\s*,\s*([^)]*)\)")


def build_environment_catalog(repo_root: Path) -> str:
    env_rows: dict[str, tuple[str, str, str]] = {}
    for path in iter_source_files(repo_root):
        relative = _relative(path, repo_root)
        for line in _read_text(path).splitlines():
            if not any(token in line for token in ("getenv", "environ", "export ")):
                continue
            names = ENV_NAME_RE.findall(line)
            if not names:
                continue
            default = ""
            default_match = GETENV_DEFAULT_RE.search(line)
            if default_match:
                default = default_match.group(2).strip().strip('"').strip("'")
            for name in names:
                env_rows.setdefault(name, (default, relative, line.strip()[:140]))

    table_rows = [
        "| Variable | Default Hint | Source | Use |",
        "| --- | --- | --- | --- |",
    ]
    for name, (default, source, use) in sorted(env_rows.items()):
        table_rows.append(f"| `{name}` | `{default or '-'}` | `{source}` | `{use.replace('|', '/')}` |")

    return "\n".join(
        [
            "# Settings And Environment Variables",
            "",
            "Generated from environment lookups and shell exports. Values shown here are hints from source code, not the current process environment.",
            "",
            "```mermaid",
            "flowchart TD",
            "    E[Environment] --> C[Settings models]",
            "    C --> A[Agent runtime]",
            "    C --> G[Gateway runtime]",
            "    C --> T[Audio runtime]",
            "    L[Launcher scripts] --> E",
            "```",
            "",
            *table_rows,
        ]
    )


def build_launcher_reference(repo_root: Path) -> str:
    candidate_paths = [
        repo_root / "setup-manual.sh",
        repo_root / "startmanual.sh",
        repo_root / "startup.sh",
        repo_root / "startup-audio.sh",
        repo_root / "install.sh",
        repo_root / "setupplaywright.sh",
        repo_root / "src/gateway_agent/startup.sh",
        repo_root / "src/gateway_macos/startup.sh",
    ]
    candidate_paths.extend(sorted((repo_root / "src/llm").glob("start*.sh")) if (repo_root / "src/llm").exists() else [])
    rows: list[tuple[str, str, str]] = []
    for path in candidate_paths:
        if not path.exists() or should_exclude_path(path):
            continue
        text = _read_text(path)
        exports = sorted(set(re.findall(r"export\s+([A-Z][A-Z0-9_]+)", text)))
        uvicorn = "uvicorn" if "uvicorn" in text else ""
        rows.append((_relative(path, repo_root), ", ".join(f"`{name}`" for name in exports[:16]) or "-", uvicorn or "-"))

    table_rows = [
        "| Script | Exported Variables | Runtime Hook |",
        "| --- | --- | --- |",
    ]
    for script, exports, hook in rows:
        table_rows.append(f"| `{script}` | {exports} | {hook} |")

    return "\n".join(
        [
            "# Shell Launcher And Export Reference",
            "",
            "Generated from startup, install, gateway, audio, and LLM launcher scripts.",
            "",
            "```mermaid",
            "flowchart LR",
            "    I[install.sh] --> V[.venv]",
            "    S[startup.sh] --> A[Agent API :8011]",
            "    S --> T[Audio API :8012]",
            "    S --> D[Manual API :8013]",
            "    B[startup-audio.sh] --> T",
            "    M[startmanual.sh] --> D",
            "    G[gateway startup] --> X[Gateway :8787]",
            "```",
            "",
            *table_rows,
        ]
    )


def build_storage_reference(repo_root: Path) -> str:
    rows: dict[str, tuple[str, str]] = {}
    for path in iter_source_files(repo_root, suffixes=(".py",)):
        relative = _relative(path, repo_root)
        text = _read_text(path)
        for db_name in sorted(set(re.findall(r"([A-Za-z0-9_./-]+\.db)", text))):
            rows.setdefault(db_name, (relative, "SQLite path/reference"))
        if "sqlite3.connect" in text:
            rows.setdefault(f"{relative}:sqlite3.connect", (relative, "SQLite connection owner"))

    table_rows = [
        "| Storage Item | Source | Notes |",
        "| --- | --- | --- |",
    ]
    for item, (source, notes) in sorted(rows.items()):
        table_rows.append(f"| `{item}` | `{source}` | {notes} |")

    return "\n".join(
        [
            "# Storage And Database Reference",
            "",
            "Generated from SQLite usage and database path literals.",
            "",
            "```mermaid",
            "flowchart TD",
            "    A[artifacts/] --> M[agent_memory.db]",
            "    A --> P[prompts.db]",
            "    A --> E[agent_events.db]",
            "    A --> G[agent_gateways.db]",
            "    A --> C[chats.db]",
            "    A --> L[learning ledger]",
            "    A --> R[runtime/cache stores]",
            "```",
            "",
            *table_rows,
        ]
    )


CAPABILITY_ID_RE = re.compile(r"capability_id\s*=\s*[\"']([^\"']+)[\"']")
DESCRIPTION_RE = re.compile(r"description\s*=\s*\((.*?)\)|description\s*=\s*[\"']([^\"']+)[\"']", re.DOTALL)


def build_capability_reference(repo_root: Path) -> str:
    rows: list[tuple[str, str, str]] = []
    capability_root = repo_root / "src/agent_runtime/capabilities"
    if capability_root.exists():
        for path in sorted(capability_root.glob("*.py")):
            if should_exclude_path(path):
                continue
            text = _read_text(path)
            for capability_id in CAPABILITY_ID_RE.findall(text):
                domain = capability_id.split(".", 1)[0]
                rows.append((capability_id, domain, _relative(path, repo_root)))

    table_rows = [
        "| Capability | Domain | Source |",
        "| --- | --- | --- |",
    ]
    for capability_id, domain, source in sorted(rows):
        table_rows.append(f"| `{capability_id}` | `{domain}` | `{source}` |")

    return "\n".join(
        [
            "# Capability And Operator Reference",
            "",
            "Generated from capability manifests. The default planning surface centers on operator actions plus deterministic runtime inspection capabilities.",
            "",
            "```mermaid",
            "flowchart LR",
            "    L[LLM proposal] --> V[Manifest validation]",
            "    V --> O[operator.*]",
            "    V --> R[runtime.*]",
            "    O --> S[Safety policy]",
            "    S --> E[Execution engine]",
            "```",
            "",
            *table_rows,
        ]
    )


def build_generated_pipeline_reference() -> str:
    return "\n".join(
        [
            "# Generated Reference Pipeline",
            "",
            "The manual service inspects source files at startup and combines generated pages with authored Markdown and copied legacy docs.",
            "",
            "```mermaid",
            "flowchart TD",
            "    A[Manual service startup] --> B[Load authored content]",
            "    A --> C[Scan source roots]",
            "    C --> D[Routes]",
            "    C --> E[Environment variables]",
            "    C --> F[Storage paths]",
            "    C --> G[Capabilities]",
            "    B --> H[Manual corpus]",
            "    D --> H",
            "    E --> H",
            "    F --> H",
            "    G --> H",
            "    H --> I[SQLite FTS5 index]",
            "    I --> J[Search API]",
            "    H --> K[Catalog and document APIs]",
            "```",
            "",
            "Generated pages are read-only API responses. Refresh them by restarting the manual service or running with `./startmanual.sh --reload` while editing source.",
        ]
    )


def generate_reference_pages(repo_root: Path) -> list[GeneratedPage]:
    """Build all generated manual reference pages."""

    root = Path(repo_root)
    return [
        GeneratedPage(
            id="generated-source-modules",
            title="Source Module Catalog",
            summary="A generated index of runtime source modules with file paths and top-level structure.",
            body_markdown=build_module_catalog(root),
            tags=("generated", "source", "modules"),
            order=10,
        ),
        GeneratedPage(
            id="generated-route-inventory",
            title="FastAPI Route Inventory",
            summary="A generated inventory of HTTP and websocket routes exposed by the local services.",
            body_markdown=build_route_inventory(root),
            tags=("generated", "api", "fastapi", "routes"),
            order=20,
        ),
        GeneratedPage(
            id="generated-settings-environment",
            title="Settings And Environment Variables",
            summary="A generated reference for environment variables and configuration lookups.",
            body_markdown=build_environment_catalog(root),
            tags=("generated", "configuration", "environment"),
            order=30,
        ),
        GeneratedPage(
            id="generated-launchers",
            title="Shell Launcher And Export Reference",
            summary="A generated reference for startup scripts and their exported runtime variables.",
            body_markdown=build_launcher_reference(root),
            tags=("generated", "launchers", "scripts"),
            order=40,
        ),
        GeneratedPage(
            id="generated-storage",
            title="Storage And Database Reference",
            summary="A generated map of SQLite stores and database paths referenced by source.",
            body_markdown=build_storage_reference(root),
            tags=("generated", "storage", "sqlite"),
            order=50,
        ),
        GeneratedPage(
            id="generated-capabilities",
            title="Capability And Operator Reference",
            summary="A generated list of capability manifests and operator-facing execution surfaces.",
            body_markdown=build_capability_reference(root),
            tags=("generated", "capabilities", "operator"),
            order=60,
        ),
        GeneratedPage(
            id="generated-reference-pipeline",
            title="Generated Reference Pipeline",
            summary="How the manual service turns source inspection into living reference pages.",
            body_markdown=build_generated_pipeline_reference(),
            tags=("generated", "manual-service", "search"),
            order=70,
        ),
    ]
