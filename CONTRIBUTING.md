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

## Tests

The suite runs offline: HTTP is mocked with `respx`, and the payloads come from
`tests/fixtures/data` — responses captured from a real PiKVM (kvmd 4.206)
rather than hand-written dictionaries, because hand-written ones are how the
library ended up with models that no real response could satisfy. Use them in
new tests:

```python
from tests.fixtures import load_json, load_result

mock_api.get("/api/atx").mock(return_value=httpx.Response(200, json=load_json("atx")))
```

`tests/test_contract.py` validates every captured response against the model
that claims to describe it. A gap the library has not closed yet is marked
`xfail(strict=True)` with its issue number, so fixing the model without
removing the marker fails the suite instead of leaving stale bookkeeping.

See [`tests/fixtures/README.md`](tests/fixtures/README.md) for what the
captures cover, what is redacted from them, and how to refresh them from your
own device.

### Running against a real device

```bash
PIKVM_URL=https://pikvm.local PIKVM_PASSWD=secret uv run pytest --live tests/live
```

`tests/live` is skipped without `--live` and skipped again if the environment
does not point at a device, so CI never touches hardware. `test_readonly.py`
is strictly read-only — the device under test is somebody's working KVM, where
an ATX call power-cycles a real host. Keep it that way when adding to it.

### Running the tests that change something

`tests/live/test_mutating.py` writes. It carries a second marker and needs a
second flag, plus the device named again in the environment:

```bash
PIKVM_URL=https://pikvm.local PIKVM_PASSWD=secret \
PIKVM_MUTATING_OK=https://pikvm.local \
  uv run pytest --live --live-mutating tests/live
```

`PIKVM_MUTATING_OK` has to equal `PIKVM_URL` character for character. A flag
on its own is one shell-history recall away from power-cycling the wrong
machine; a URL that has to be typed out again is not.

Three groups reach past kvmd to the hardware or to other people's sessions,
and each needs its own variable on top of that:

| Variable | What it lets run |
| --- | --- |
| `PIKVM_MUTATING_MSD` | the mass storage lifecycle, which needs the OTG mass-storage function on — that attaches a USB drive to the attached host |
| `PIKVM_MUTATING_GPIO` | moving an output off the state it was found in; on a stock v3 that is the USB breaker |
| `PIKVM_MUTATING_LOGOUT` | kvmd's logout, which closes **every** session of the user, browser tabs included |

Every test there restores what it changed, in a fixture teardown or a
`finally`. Keep that property: assert on the state kvmd reports afterwards
rather than on the status code, and leave the device where you found it.

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
