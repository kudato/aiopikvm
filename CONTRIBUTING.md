# Contributing to aiopikvm

## Development setup

```bash
git clone https://github.com/kudato/aiopikvm.git
cd aiopikvm
uv sync
```

## Development workflow

```bash
# Lint
uv run ruff check src/ tests/

# Format
uv run ruff format src/ tests/

# Check formatting (CI mode)
uv run ruff format --check src/ tests/

# Type check
uv run mypy src/

# Run tests
uv run pytest

# Run a specific test file
uv run pytest tests/test_atx.py -v
```

All checks must pass before submitting a pull request. Ruff handles both linting and auto-formatting.

## Making changes

1. Fork the repository
2. Create a feature branch from `main`
3. Make your changes
4. Run all checks: `uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run mypy src/ && uv run pytest`
5. Commit using [Conventional Commits](https://www.conventionalcommits.org/): `feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`
6. Open a pull request against `main`

## Code style

### Imports and annotations

- `from __future__ import annotations` only in modules that use `TYPE_CHECKING` forward references
- `TYPE_CHECKING` guards for imports only needed by type checkers

### Type definitions

- All public API must be fully typed (mypy strict mode)
- Use modern union syntax `X | Y` (not `Optional[X]` or `Union[X, Y]`)

### Docstrings

- Google-style docstrings on all public classes and methods
- Module docstrings on every file

### Naming conventions

- Internal modules prefixed with `_` (`_client.py`, `_base_resource.py`, etc.)
- Resource classes: `{Subsystem}Resource` (e.g., `ATXResource`, `HIDResource`)
- Model classes: descriptive PascalCase (e.g., `ATXState`, `GPIOChannel`)

### Ruff configuration

- Rules: `["E", "F", "I", "UP", "B", "W", "RUF"]`
- Line length: 88
- Target: Python 3.13

## Project structure

```
src/aiopikvm/
├── __init__.py              # Public API re-exports
├── _client.py               # PiKVM class — entry point
├── _base_resource.py        # BaseResource — base for all resources
├── _constants.py            # Default constants
├── _exceptions.py           # Exception hierarchy
├── _ws.py                   # WebSocket client
├── py.typed                 # PEP 561 marker
├── models/                  # Pydantic response models
│   ├── _base.py             # Base model with extra="allow"
│   ├── atx.py, hid.py, ... # One file per subsystem
└── resources/               # API resource classes
    ├── auth.py, atx.py, ... # One file per subsystem
```

## Releasing

For maintainers:

1. Update `version` in `pyproject.toml`
2. Commit: `chore: bump version to X.Y.Z`
3. Tag: `git tag vX.Y.Z`
4. Push: `git push origin main --tags`

CI will automatically build and publish to PyPI.
