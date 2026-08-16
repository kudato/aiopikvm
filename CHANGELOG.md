# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added

- Typed exceptions for the failures kvmd actually distinguishes: `BusyError`
  (HTTP 409, kvmd's `IsBusyError` — ATX, MSD and GPIO all raise it while an
  earlier operation is still running), `UnavailableError` (HTTP 503, subsystem
  disabled or offline), `RedirectError` (3xx), `ResponseError` (a payload the
  client cannot parse) and `ConfigurationError` (unusable URL or credentials).
  Everything except `ConfigurationError` derives from `APIError`, so existing
  `except APIError` handlers keep catching them (#64).
- `APIError.error` and `APIError.error_msg` expose kvmd's error class name and
  human-readable message from `{"ok": false, "result": {"error": ...,
  "error_msg": ...}}`; previously only the class name reached the caller, and
  only inside the message string (#64).
- Per-call `timeout` on the internal resource helpers and on the public calls
  that block server-side: every `ATXResource` action (a long power click holds
  the button for 5.5 s of the 10 s client default), `MSDResource.upload()` and
  `HIDResource.type_text()` (#66).
- `follow_redirects` client option for setups where a proxy legitimately
  redirects (#67).

### Changed

- **Breaking:** `PiKVMWebSocket` raises `ConfigurationError` instead of
  `ValueError` for a URL scheme other than `http`/`https`, so that scheme
  mistakes stay inside the `PiKVMError` hierarchy (#65).
- A non-JSON or non-object response body now raises `ResponseError` rather than
  a plain `APIError`. `ResponseError` derives from `APIError`, so existing
  handlers are unaffected (#45).

### Removed

- **Breaking:** the `wait` parameter of `HIDResource.send_shortcut`.
  kvmd never reads it — the inter-event delay is hardcoded server-side
  (50 ms) — so the parameter never had any effect (#31).

### Fixed

- `get_state()` let pydantic's `ValidationError` escape when a response did not
  match its model — outside the documented `PiKVMError` hierarchy, so
  `except PiKVMError` did not catch it. Model validation now raises
  `ResponseError` and names the endpoint and model (#45).
- Only `httpx.ConnectError` and `httpx.TimeoutException` were translated. Every
  other transport failure escaped raw, including the `RemoteProtocolError` kvmd
  produces on each restart (it drops in-flight connections after a one-second
  shutdown timeout), `ReadError`/`WriteError`, `UnsupportedProtocol` for a
  scheme-less URL, and `UnicodeEncodeError` for non-ASCII credentials (#65).
- A 3xx response was fed to the JSON parser and surfaced as
  `APIError("Invalid JSON response")`, or as a bogus success from the raw and
  streaming helpers. kvmd redirects doubled and trailing slashes, and PiKVM's
  nginx redirects `http://` to `https://` (#67).
- `PiKVM.stream()` raised `httpx.ResponseNotRead` instead of an aiopikvm error
  when the server answered a stream with an error status: the status check read
  a body that had not been fetched (#35).
- `PiKVM.stream()` passed `timeout=None` straight to httpx, which disables
  timeouts altogether, instead of falling back to the client default (#66).
- `HIDResource.send_shortcut` was passing keys as a list, which httpx
  serialises as repeated query params (`keys=A&keys=B`); kvmd reads only
  the first value, so every chord was silently delivered as a single lone
  key. Keys are now sent as one comma-separated value, and calling
  `send_shortcut()` with no keys raises `ValueError` instead of becoming
  a silent server-side no-op (#26).
- `SystemResource.get_info` had the same serialisation bug for `fields`:
  requesting several categories silently returned only the first one.
  Fields are now comma-joined; omitting them still returns all
  categories (#30).

## [0.2.1] — 2026-05-05

### Fixed

- `HIDResource.send_shortcut` was sending the multi-value query parameter as
  `key=...` (singular); kvmd validates `keys` (plural) and rejected the
  request with HTTP 400 (#19).
- `StreamerState` model rewritten to match the actual `/api/streamer`
  response: top-level `features` / `limits` / `params` / `snapshot` /
  `streamer` (the latter is `null` when no stream clients are connected).
  The previous top-level `enabled` and `source` fields never existed in the
  API and `get_state()` always raised `pydantic.ValidationError` (#18).
- `StreamerResource.ocr()` was calling the wrong endpoint
  (`/api/streamer/ocr`, which returns capability metadata) and parsing
  capability JSON as a string. It now sends
  `GET /api/streamer/snapshot?ocr=1` and returns plain text. Added a
  `langs` parameter (comma-separated by kvmd) and a 30 s default per-call
  timeout — Tesseract on the Pi takes 10–20 s for full-screen OCR,
  exceeding the client default (#21).

### Added

- `StreamerResource.get_ocr_info()` and `OCRInfo` / `OCRLangs` models for
  `GET /api/streamer/ocr` capability metadata (replaces the
  always-broken `OCRResult`).
- `allow_offline` parameter on `StreamerResource.snapshot()` and
  `StreamerResource.ocr()`. When the video source is offline (host asleep,
  HDMI unplugged) but the streamer process is still running, kvmd returns
  HTTP 503 by default; with `allow_offline=True` it returns a
  "NO LIVE VIDEO" placeholder JPEG (or its OCR'd text). The flag has no
  effect when the streamer process is fully stopped — that is a kvmd
  lifecycle limitation (#22).
- Optional `timeout` argument on `PiKVM.request()` and
  `BaseResource._get_raw()` for per-call timeout overrides.

### Changed

- New typed submodels for streamer state: `Streamer`, `StreamerEncoder`,
  `StreamerH264`, `StreamerSinkInfo`, `StreamerSinks`, `StreamerStream`,
  `StreamerLimits`, `StreamerLimitRange`, `StreamerFeatures`,
  `StreamerParams`, `StreamerSnapshot`. `StreamerSource` now exposes
  `captured_fps` and `desired_fps` in addition to `online` and
  `resolution`.

### Removed

- `OCRResult` model — replaced by `OCRInfo` (capability metadata) and a
  plain `str` return from `ocr()`. The old model never matched any real
  response.

## [0.1.1] — 2026-02-16

### Added

- Initial release
- Resources: auth, atx, hid, msd, gpio, streamer, switch, redfish, prometheus
- WebSocket client for realtime events and HID input
- Pydantic v2 response models
- Exception hierarchy
