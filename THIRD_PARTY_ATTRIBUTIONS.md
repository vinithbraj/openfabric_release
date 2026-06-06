# Third-Party Attributions

Last reviewed: 2026-06-05

This file lists third-party components that are declared, bundled, built, or
downloaded by this repository. It is informational and does not replace the
license text or package metadata distributed by each upstream project.

Except for the bundled frontend files explicitly listed below, this repository
does not redistribute third-party binaries, model weights, browsers, operating
system packages, or build/runtime dependencies. Components such as Chromium,
ffmpeg, whisper.cpp, Whisper model files, Docker base-image packages, and Python
packages are installed, built, or downloaded separately from their upstream
sources by setup scripts, package managers, Docker builds, or user-controlled
runtime workflows, and remain subject to their own upstream licenses and terms.
All third-party names, trademarks, and service marks are the property of their
respective owners.

## Bundled Frontend Components

These files are checked into this repository and redistributed with the Python
package/static assets.

| Component | Use | License | Attribution / Source |
| --- | --- | --- | --- |
| Mermaid | Diagram rendering in the Agent UI and manual | MIT | Copyright (c) 2014-2022 Knut Sveidqvist. License files: `src/agent_runtime/api/static/agent_ui/vendor/mermaid/LICENSE`, `src/manual/static/vendor/mermaid/LICENSE`. Source: https://github.com/mermaid-js/mermaid |
| xterm.js | Browser terminal UI in the Agent UI | MIT | Copyright (c) 2017-2019 xterm.js authors, 2014-2016 SourceLair Private Company, 2012-2013 Christopher Jeffrey. License file: `src/agent_runtime/api/static/agent_ui/vendor/xterm/LICENSE`. Source: https://github.com/xtermjs/xterm.js |

## Python Dependencies

These are direct Python dependencies declared in `pyproject.toml`. Transitive
dependencies are resolved by the installer and retain their own upstream
licenses and metadata.

### Runtime

| Component | Requirement | License |
| --- | --- | --- |
| FastAPI | `fastapi>=0.116,<1.0` | MIT |
| NumPy | `numpy>=2.0,<3.0` | BSD-3-Clause and other permissive licenses in package metadata |
| pandas | `pandas>=2.2,<3.0` | BSD-3-Clause |
| Playwright for Python | `playwright>=1.49,<2.0` | Apache-2.0 |
| Pydantic | `pydantic>=2.11,<3.0` | MIT |
| PyYAML | `pyyaml>=6.0.2,<7.0` | MIT |
| Rich | `rich>=14.0,<15.0` | MIT |
| SQLGlot | `sqlglot>=26.0,<27.0` | MIT |
| tabulate | `tabulate>=0.9,<1.0` | MIT |
| Typer | `typer>=0.16,<1.0` | MIT |
| Uvicorn | `uvicorn[standard]>=0.35,<1.0` | BSD-3-Clause |
| websockets | `websockets>=16.0,<17.0` | BSD-3-Clause |
| wsproto | `wsproto>=1.2,<2.0` | MIT |

### Optional Manual Dependency

| Component | Requirement | License |
| --- | --- | --- |
| markdown-it-py | `markdown-it-py>=3,<4` | MIT |

### Development And Tooling Dependencies

| Component | Requirement | License |
| --- | --- | --- |
| Bandit | `bandit>=1.8,<2.0` | Apache-2.0 |
| Black | `black>=25.0,<26.0` | MIT |
| build | `build>=1.2,<2.0` | MIT |
| coverage.py | `coverage>=7.0,<8.0` | Apache-2.0 |
| Flake8 | `flake8>=7.0,<8.0` | MIT |
| HTTPX | `httpx>=0.27,<1.0` | BSD-3-Clause |
| IPython | `ipython>=9.0,<10.0` | BSD-3-Clause |
| isort | `isort>=6.0,<7.0` | MIT |
| mypy | `mypy>=1.10,<2.0` | MIT |
| openpyxl | `openpyxl>=3.1,<4.0` | MIT |
| pip-audit | `pip-audit>=2.7,<3.0` | Apache-2.0 |
| pipdeptree | `pipdeptree>=2.20,<3.0` | MIT |
| pre-commit | `pre-commit>=4.0,<5.0` | MIT |
| pyarrow | `pyarrow>=15.0,<23.0` | Apache-2.0 |
| pylint | `pylint>=3.0,<5.0` | GPL-2.0-or-later |
| pyright | `pyright>=1.1,<2.0` | MIT |
| pytest | `pytest>=8.0,<9.0` | MIT |
| pytest-cov | `pytest-cov>=5.0,<8.0` | MIT |
| Ruff | `ruff>=0.8,<1.0` | MIT |
| Twine | `twine>=6.0,<7.0` | Apache-2.0 |

## Built Or Downloaded Audio Components

The repository does not redistribute these audio artifacts. Setup scripts and
the all-services Docker build may build or download them on the user's machine.

| Component | How It Is Used | License / Terms | Source |
| --- | --- | --- | --- |
| whisper.cpp | `scripts/build-whisper-cli.sh` clones and builds `whisper-cli` when audio build is enabled or local setup needs it | MIT | https://github.com/ggml-org/whisper.cpp |
| Whisper ggml model files | `setup-audio-transcriber.sh` and Docker audio entrypoints download model files into local artifacts or `/data/models/whisper` | Upstream model terms apply; not redistributed by this repository | https://huggingface.co/ggerganov/whisper.cpp |

## Browser, Container, And System Components

These third-party components are installed by setup or Docker workflows rather
than checked into the repository.

| Component | How It Is Used | License / Terms |
| --- | --- | --- |
| Playwright Chromium browser | Installed by `python -m playwright install chromium` for browser automation | Chromium and Playwright upstream notices apply |
| Ubuntu 24.04 base image | Base image for Docker builds | Ubuntu package and image terms apply |
| Docker image system packages | Docker installs packages such as `build-essential`, `ca-certificates`, `cmake`, `curl`, `ffmpeg`, `git`, `openssl`, `python3`, `python3-pip`, `python3-venv`, and `sqlite3` | Each Ubuntu package retains its own upstream license metadata |

## Notes

- Local files under `.aor/`, `artifacts/models/`, and `/data/models/whisper`
  are generated, built, or downloaded during setup and are not intended to be
  committed.
- Optional user-provided audio assets under `vendor/audio-transcriber/` are
  ignored by git and are not part of the distributed repository.
- For a public release, publish from a clean history that never contained
  removed third-party binary/model blobs.
