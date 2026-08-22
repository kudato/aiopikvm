# CLAUDE.md

## Git

- Open every pull request against `main`, never against another branch; keep
  dependent work local until what it needs is merged
- Write commits and pull request titles as Conventional Commits, `!` included;
  the title becomes the squash commit subject on `main`
- Update a branch from `main` by merging, never by rebasing
- Never rewrite pushed history — no force push, rebase, amend or hard reset on
  a published branch
- Squash-merge every pull request; `main` stays linear

## Commands

```bash
uv sync --all-groups                   # install all deps (dev + docs)
uv run ruff check src/ tests/          # lint
uv run ruff format src/ tests/         # auto-format
uv run mypy src/                       # type check (strict)
uv run pytest                          # run tests
uv run mkdocs build                    # build static docs
```

## Code style

- `from __future__ import annotations` only where `TYPE_CHECKING` forward
  references need it (`_client.py`, `_base_resource.py`, `_webrtc.py`), not
  everywhere
- Use `X | Y` unions and `type` aliases, never `Union[X, Y]` or `Optional[X]`
- Break circular imports with `TYPE_CHECKING` guards, and import resource
  classes lazily inside the `@cached_property` getter of `PiKVM`; that getter
  is all a new resource needs, since `_RESOURCE_NAMES` is derived from them
- Response models inherit `_Base` (`extra="allow"`), one file per subsystem
- Validate payloads through `BaseResource._get_model()` / `_validate()`, never
  `Model.model_validate()` in a resource — a bare pydantic `ValidationError`
  escapes the documented hierarchy
- Nothing outside `PiKVMError` may reach a caller: httpx and pydantic failures
  are wrapped in `PiKVM.request()` / `stream()` and in `BaseResource`
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
