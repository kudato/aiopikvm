# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

**aiopikvm** — async Python client for the PiKVM API, published on PyPI. Covers the full PiKVM API surface: ATX, HID, MSD, GPIO, Streamer, Switch, Redfish, Prometheus, WebSocket.

## Tech stack

- **Python** 3.13+ (modern syntax: `X | Y` unions, `type` aliases)
- **httpx** — async HTTP client
- **websockets** — WebSocket client for `/api/ws`
- **pydantic** v2 — response models
- **uv** — package/project manager
- **ruff** — linting and formatting
- **pytest** + **pytest-asyncio** — test framework
- **respx** — httpx request mocking in tests
- **mypy** — strict type checking

## Project structure

```
src/aiopikvm/
├── __init__.py              # Public API — re-exports PiKVM, models, exceptions
├── _client.py               # PiKVM class — entry point, httpx session, HTTP logic
├── _base_resource.py        # BaseResource — base class for all API resources
├── _constants.py            # DEFAULT_TIMEOUT, DEFAULT_VERIFY_SSL
├── _exceptions.py           # Exception hierarchy (PiKVMError → APIError, AuthError, etc.)
├── _ws.py                   # PiKVMWebSocket — realtime events and HID input
├── py.typed                 # PEP 561 marker
├── models/
│   ├── __init__.py          # Module docstring only
│   ├── _base.py             # _Base(BaseModel) with extra="allow"
│   ├── atx.py               # ATXLeds, ATXState
│   ├── hid.py               # HIDMouse, HIDKeyboard, HIDState, HIDKeymap
│   ├── msd.py               # MSDDrive, MSDStorage, MSDState
│   ├── gpio.py              # GPIOChannel, GPIOInput, GPIOState
│   ├── streamer.py          # StreamerState, Streamer (+ submodels), StreamerSource, Resolution, OCRInfo, OCRLangs
│   └── switch.py            # SwitchPort, SwitchState, EDID
└── resources/
    ├── __init__.py           # Module docstring only
    ├── auth.py               # AuthResource — login/check/logout
    ├── atx.py                # ATXResource — host power control
    ├── hid.py                # HIDResource — keyboard, mouse, text input
    ├── msd.py                # MSDResource — virtual drives, image upload
    ├── gpio.py               # GPIOResource — GPIO channel control
    ├── streamer.py           # StreamerResource — snapshots, OCR
    ├── switch.py             # SwitchResource — multi-port KVM, EDID
    ├── redfish.py            # RedfishResource — DMTF BMC compatibility
    └── prometheus.py         # PrometheusResource — metrics export
tests/
├── conftest.py              # Fixtures: mock_api (respx), client (PiKVM)
├── test_client.py           # PiKVM lifecycle, auth headers, resource access
├── test_auth.py             # AuthResource tests
├── test_atx.py              # ATXResource tests
├── test_hid.py              # HIDResource tests
├── test_msd.py              # MSDResource tests
├── test_gpio.py             # GPIOResource tests
├── test_streamer.py         # StreamerResource tests
├── test_switch.py           # SwitchResource tests
├── test_redfish.py          # RedfishResource tests
├── test_prometheus.py       # PrometheusResource tests
├── test_ws.py               # PiKVMWebSocket tests
├── test_errors.py           # Network/HTTP error handling tests
├── test_contract.py         # Captured responses vs models (strict xfail per open issue)
├── helpers.py               # Assertions shared by the mocked and live suites
├── fixtures/
│   ├── __init__.py          # load_json / load_result / load_text / load_jsonl / manifest
│   ├── capture.py           # `python -m tests.fixtures.capture` — refresh + sanitize
│   ├── README.md            # Provenance, redaction rules, caveats
│   └── data/                # Sanitized real kvmd 4.186 responses + _manifest.json
└── live/                    # Opt-in read-only suite against a real device (--live)
```

## Commands

```bash
uv sync                              # Install dependencies
uv run pytest                        # Run all tests
uv run pytest tests/test_atx.py -v   # Run a specific test file
uv run ruff check src/ tests/        # Lint
uv run ruff format src/ tests/       # Format
uv run ruff format --check src/ tests/  # Check formatting without modifying
uv run mypy src/                     # Type check (strict)

# Against a real device (skipped without --live and without the env vars)
PIKVM_URL=... PIKVM_PASSWD=... uv run pytest --live tests/live
PIKVM_URL=... PIKVM_PASSWD=... uv run python -m tests.fixtures.capture
```

## Architecture patterns

### Client lifecycle

`PiKVM` is an async context manager that creates an `httpx.AsyncClient` on `__aenter__` and closes it on `__aexit__`:

```python
async with PiKVM("https://pikvm.local", user="admin", passwd="admin") as kvm:
    await kvm.atx.power_on()
```

### External httpx client

An external `httpx.AsyncClient` can be passed via `http_client=`. In this case `PiKVM` does **not** close it on exit — the caller is responsible.

### Explicit `aclose()`

`PiKVM` provides an explicit `aclose()` method that clears cached resources and closes the HTTP client. `__aexit__` delegates to `aclose()`.

### Resource access via `@cached_property`

Each resource (`atx`, `hid`, `msd`, etc.) is a `@cached_property` that:
1. Performs a lazy import of the resource class (avoids circular imports)
2. Calls `_ensure_client()` to verify the async context is active
3. Passes `self` (the `PiKVM` instance) to the resource constructor

Resources are cleared from `__dict__` on `__aexit__`.

### TYPE_CHECKING for type hints

Resource type annotations in `_client.py` use `TYPE_CHECKING` guards to avoid importing resource modules at runtime (they are lazily imported inside `@cached_property` methods).

### Resource class hierarchy

All resources inherit from `BaseResource`:
- `BaseResource.__init__(self, client: PiKVM)` — stores the `PiKVM` instance
- `_request()` → delegates to `PiKVM.request()`, parses the JSON envelope
- `_get()`, `_post()`, `_delete()`, `_patch()` — convenience wrappers
- `_get_raw()` → calls `PiKVM.request()`, returns raw `httpx.Response`

### Error handling

HTTP logic is centralized in `PiKVM.request()`:
- `httpx.ConnectError` → `ConnectError`
- `httpx.TimeoutException` → `ConnectionTimeoutError`
- HTTP 401/403 → `AuthError` (via `_raise_for_status`)
- HTTP >= 400 → `APIError` (via `_raise_for_status`)

`BaseResource._request()` additionally checks the JSON body:
- `{"ok": false, "result": {"error": "..."}}` → `APIError`

### PiKVM response format

Standard PiKVM API responses use the envelope `{"ok": true, "result": {...}}`. The `BaseResource._request()` method unwraps `result` and raises `APIError` when `ok` is `false`.

**Exception:** Redfish endpoints return plain JSON — `RedfishResource` calls `self._client.request()` directly instead of `_request()`.

### SSL

SSL verification is **off** by default (`DEFAULT_VERIFY_SSL = False`) because PiKVM typically uses self-signed certificates.

### WebSocket

`PiKVMWebSocket` connects to `/api/ws?stream=0`, converting `https://` → `wss://` and `http://` → `ws://`. After connection the server sends a full state bundle, then incremental updates. The client can send key/mouse/ping events.

## Code style

### Imports and annotations

- `from __future__ import annotations` only in modules that use `TYPE_CHECKING` forward references (`_client.py`, `_base_resource.py`)
- `TYPE_CHECKING` guards for imports only needed by type checkers

### Type definitions

- All public API is fully typed (mypy strict mode)
- Use modern union syntax `X | Y` (not `Optional[X]` or `Union[X, Y]`)

### Pydantic models

- All models inherit from `_Base` (defined in `models/_base.py`) which sets `extra="allow"`
- One file per API subsystem in `models/`
- `models/__init__.py` contains only a module docstring

### Docstrings

- Google-style docstrings on all public classes and methods
- Module docstrings on every file

### Naming

- Internal modules prefixed with `_` (`_client.py`, `_base_resource.py`, etc.)
- Resource classes: `{Subsystem}Resource` (e.g., `ATXResource`, `HIDResource`)
- Model classes: descriptive PascalCase (e.g., `ATXState`, `GPIOChannel`)

## Testing patterns

### Fixtures

- `mock_api` — `respx.MockRouter` with `base_url="https://pikvm.local"`
- `client` — `PiKVM` instance created inside `async with`, depends on `mock_api`

### Test structure

One test file per resource (`test_atx.py`, `test_hid.py`, etc.) plus `test_client.py` for lifecycle and `test_errors.py` for error handling.

### Mocking routes

Mock with a captured response, not a hand-written dict — hand-written payloads
are why several models shipped unable to parse anything a real device sends:

```python
from tests.fixtures import load_json

mock_api.get("/api/atx").mock(
    return_value=httpx.Response(200, json=load_json("atx"))
)
```

`load_result("atx")` returns the unwrapped `result`; `load_text` and
`load_jsonl` cover the plain-text and JSON Lines captures. `manifest()`
describes every capture (method, path, params, status, content type).

### Contract tests

`test_contract.py` validates each captured response against its model. Cases
the library cannot handle yet carry `xfail(reason="... (#NN)", strict=True)`.
When a fix lands, remove the marker in the same PR — strict xfail turns a
silent pass into a failure.

### Live device suite

`tests/live` runs only with `pytest --live` and only when `PIKVM_URL` /
`PIKVM_PASSWD` are set, so CI never touches hardware. It must stay read-only:
no ATX, MSD or HID calls. `tests/fixtures/capture.py` refreshes the captures
from a device, redacting serials, host names and addresses; it refuses to
write a file where a secret survived.

### Exception testing

```python
mock_api.get("/api/atx").mock(side_effect=httpx.ConnectError("refused"))
with pytest.raises(ConnectError, match="refused"):
    await client.atx.get_state()
```

## Gotchas

### PiKVM authentication

Auth uses `X-KVMD-User` / `X-KVMD-Passwd` headers. When TOTP is enabled, the code is concatenated to the password **without a separator**: `f"{passwd}{totp}"`.

### Circular imports

`_client.py` imports resource classes only inside `@cached_property` bodies (lazy) and uses `TYPE_CHECKING` for type annotations. `_base_resource.py` imports `PiKVM` under `TYPE_CHECKING` only.

### Redfish non-standard format

Redfish endpoints do **not** return `{"ok": true, "result": ...}`. `RedfishResource` calls `self._client.request()` directly and parses the JSON itself.

### respx base_url matching

The `mock_api` fixture uses `base_url="https://pikvm.local"` — this must match the URL passed to `PiKVM(...)` in the `client` fixture exactly.

## Commit style

Conventional Commits: `feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`.

## Common tasks

### Adding a new resource

1. Create `src/aiopikvm/resources/newresource.py` with a class inheriting `BaseResource`
2. Add `from __future__ import annotations` at the top if using `TYPE_CHECKING` forward references
3. Add `@cached_property` with lazy import in `_client.py`
4. Add the resource name to `_RESOURCE_NAMES` tuple in `_client.py`
5. Add tests in `tests/test_newresource.py`

### Adding a new model

1. Create or edit a file in `src/aiopikvm/models/` (inherit from `_Base`)
2. Import directly from the model file in `__init__.py` and add to `__all__`
