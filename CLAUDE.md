# CLAUDE.md

## Commands

```bash
uv sync                                # install all deps (dev + docs)
uv run ruff check src/ tests/          # lint
uv run ruff format src/ tests/         # auto-format
uv run mypy src/                       # type check (strict)
uv run pytest                          # run tests
uv run mkdocs build                    # build static docs
```

## Code style

- `from __future__ import annotations` only where `TYPE_CHECKING` forward
  references need it (`_client.py`, `_base_resource.py`), not everywhere
- Use `X | Y` unions and `type` aliases, never `Union[X, Y]` or `Optional[X]`
- Break circular imports with `TYPE_CHECKING` guards, and import resource
  classes lazily inside the `@cached_property` getter of `PiKVM`
- A new resource also goes into `_RESOURCE_NAMES`, or `aclose()` leaves it
  cached on the client
- Response models inherit `_Base` (`extra="allow"`), one file per subsystem
- Google-style docstrings with `Args:`, `Returns:` and `Raises:` sections on
  every module, public class and method
- Redfish endpoints do not use the `{"ok": ..., "result": ...}` envelope:
  `RedfishResource` calls `PiKVM.request()` directly, everything else goes
  through `BaseResource`

## Tests

- Mock with a response captured from a real device (`tests/fixtures`, see its
  README), never a hand-written dict. Hand-written payloads are why five
  subsystems shipped with a `get_state()` no real response could satisfy, and
  a green suite throughout
- `asyncio_mode = "auto"` — no async markers needed
- One test file per resource; `mock_api` (respx) and `client` fixtures
- Mark a gap between a model and a capture with
  `xfail(reason="... (#NN)", strict=True)` in `test_contract.py`, and delete
  the marker in the PR that fixes the model
- Never run `tests/live` or `tests/fixtures/capture.py` without the device
  owner's explicit permission — both talk to real hardware
