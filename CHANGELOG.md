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
- `HIDResource.get_inactivity()` for `GET /api/hid/inactivity` — seconds since
  the last key or mouse event on the device, from any client (#47).
- `jiggler` parameter on `HIDResource.set_params()`, which toggles kvmd's
  anti-idle mouse mover. Its state is now typed as `HIDState.jiggler` (#48).
- `keymap` and `delay` parameters on `HIDResource.type_text()`. `keymap`
  selects the layout used to translate the text, which matters whenever the
  device-wide default is not `en-us`; `delay` sets the inter-key sleep
  (0 to 5 s) that `slow` could previously only pin to 0.02 s (#37).
- `HIDKeyboardLeds`, `HIDOutputs` and `HIDJiggler` models for the parts of the
  HID state that were previously reachable only as untyped extras (#36).
- `image` parameter on `MSDResource.set_params()`: a stored name puts the image
  into the drive, `""` ejects it, a URL points at a remote one. Without it an
  uploaded image could not be selected at all (#49).
- `MSDResource.download()` streams a stored image back from `GET /msd/read`,
  optionally compressed with lzma or zstd. Its read timeout is disabled by
  default — an image transfer outlives the client default many times over (#50).
- `remove_incomplete` and `prefix` parameters on `MSDResource.upload()`: the
  first tells kvmd to delete a partially written image when the connection
  breaks, the second writes into a subdirectory of the storage (#39).
- `MSDImage`, `MSDDriveImage`, `MSDPart`, `MSDUpload` and `MSDDownload` models
  for the parts of the MSD state that had no types at all (#38).

### Changed

- **Breaking:** `HIDKeyboard` and `HIDMouse` replace `connected` with the
  fields kvmd actually sends: `online`, `outputs` and — on the keyboard —
  `leds`. `HIDState` gains `enabled` and `jiggler`, and keeps the nullable
  top-level `connected` (#36).
- **Breaking:** `MSDState.drive` and `MSDState.storage` are now optional, and
  both blocks follow what kvmd sends: `MSDStorage` carries `images`, `parts`,
  `downloading` and `uploading` instead of the invented `size`/`free`, and
  `MSDDrive.image` is a nested object rather than a string. Free space lives
  per partition, in `storage.parts[""]` for the root one (#38).
- **Breaking:** `HIDResource.get_keymaps()` returns a typed `HIDKeymaps`
  (`default`, `available`) instead of the raw envelope dict, so callers no
  longer index `result["keymaps"]["available"]` themselves (#75).
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
- **Breaking:** the `HIDKeymap` model. Its single `name` field described
  nothing kvmd emits, and no method ever returned it; `HIDKeymaps` replaces
  it (#75).

### Fixed

- `HIDResource.get_state()` raised `ResponseError` against every real device:
  `HIDKeyboard.connected` and `HIDMouse.connected` were required, but no kvmd
  HID backend nests `connected` under `keyboard` or `mouse` — it exists only
  at the top level. The mocked test passed because its payload was
  hand-written; the models now follow a capture from kvmd 4.186 (#36).
- `MSDResource.get_state()` failed against every real device. `drive` and
  `storage` were required but kvmd nulls both while the MSD is offline;
  `MSDStorage` demanded `size` and `free`, which kvmd does not send; and
  `MSDDrive.image` was typed as a string where kvmd sends an object. All three
  are fixed against captures from a device with the MSD switched on (#38).
- **Breaking:** `MSDResource.upload()` never worked with an async iterator:
  httpx framed the body as `Transfer-Encoding: chunked`, kvmd reads the image
  size from `Content-Length` and answered `HTTP 400: None argument is not a
  valid int`. A streamed upload now has to pass `size`, and raises
  `ConfigurationError` when it is missing or disagrees with the data — an
  undercount would otherwise leave a truncated image on the device marked
  `complete`, and surface as h11's `LocalProtocolError` from outside the
  `PiKVMError` hierarchy. The mocked test could not catch any of this: respx
  does not care how a body is framed (#39).
- `HIDResource.type_text()` silently truncated at 1024 characters. `limit=0`
  was documented as unlimited but omitted the query parameter, leaving kvmd's
  own default of 1024 in force; `limit` is now always sent. kvmd answers only
  once the whole string is typed, so a text that used to stop at 1024
  characters may now need a wider `timeout` than the client default (#37).
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
