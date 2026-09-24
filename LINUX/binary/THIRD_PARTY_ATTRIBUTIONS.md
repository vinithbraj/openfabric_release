# Third-Party Attributions

Dependency inventory updated: 2026-09-15

This file lists third-party components that are declared, bundled, built, or
downloaded by this repository. It is informational and does not replace the
license text or package metadata distributed by each upstream project.

Except for the bundled frontend files explicitly listed below, this repository
does not redistribute third-party binaries, model weights, browsers, operating
system packages, or build/runtime dependencies. Components such as Chromium and Python
packages are installed, built, or downloaded separately from their upstream
sources by setup scripts, package managers or user-controlled
runtime workflows, and remain subject to their own upstream licenses and terms.
All third-party names, trademarks, and service marks are the property of their
respective owners.

## Bundled Frontend Components

These files are checked into this repository and redistributed with the Python
package/static assets.

| Component | Use | License | Attribution / Source |
| --- | --- | --- | --- |
| Mermaid | Diagram rendering in the manual | MIT | Copyright (c) 2014-2022 Knut Sveidqvist. License file: `src/manual/static/vendor/mermaid/LICENSE`. Source: https://github.com/mermaid-js/mermaid |

## Python Dependencies

These are direct Python dependencies declared in `pyproject.toml`. Transitive
dependencies are resolved by the installer and retain their own upstream
licenses and metadata.

### Runtime

| Component | Requirement | License |
| --- | --- | --- |
| jsonschema | `jsonschema>=4.23,<5.0` | MIT |
| FastAPI | `fastapi>=0.116,<1.0` | MIT |
| psutil | `psutil>=5.9,<8.0` | BSD-3-Clause |
| Pydantic | `pydantic>=2.11,<3.0` | MIT |
| Google Auth | `google-auth>=2.30,<3.0` | Apache-2.0 |
| PyYAML | `pyyaml>=6.0.2,<7.0` | MIT |
| Requests | `requests>=2.32,<3.0` | Apache-2.0 |
| Rich | `rich>=14.0,<15.0` | MIT |
| regex | `regex>=2024.11.6,<2027` | Apache-2.0 AND CNRI-Python |
| SQLGlot | `sqlglot>=26.0,<27.0` | MIT |
| Typer | `typer>=0.16,<1.0` | MIT |
| Uvicorn | `uvicorn[standard]>=0.35,<1.0` | BSD-3-Clause |
| websockets | `websockets>=16.0,<17.0` | BSD-3-Clause |

### Test / E2E Dependency

| Component | Requirement | License |
| --- | --- | --- |
| Playwright for Python | `playwright>=1.49,<2.0` | Apache-2.0 |

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

## Browser And System Components

These third-party components are installed by setup workflows rather
than checked into the repository.

| Component | How It Is Used | License / Terms |
| --- | --- | --- |
| Playwright Chromium browser | Installed by `python -m playwright install chromium` for browser automation | Chromium and Playwright upstream notices apply |

## Notes

- Local files under `.aor/` and `artifacts/models/`
  are generated, built, or downloaded during setup and are not intended to be
  committed.
- For a public release, publish from a clean history that never contained
  removed third-party binary/model blobs.
