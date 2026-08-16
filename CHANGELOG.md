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
- Documented the kvmd error class names accurately: a busy ATX reports
  `AtxIsBusyError`, and a disabled one `AtxDisabledError` with HTTP 400 — not
  `UnavailableError`, which the error-handling guide claimed. The names the
  docstrings and tests used before existed nowhere in kvmd.
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
- `wait` parameter on `GPIOResource.switch()` and `pulse()`. Without it kvmd
  answers as soon as the action starts and writes whatever goes wrong after
  that — an offline driver above all — to its own log only; with it the call
  blocks until the action finishes and those failures reach the caller. A busy
  channel raises `BusyError` either way. Both calls also take a per-call
  `timeout`, since a waited pulse can outlast the client default (#51).
- `StreamerResource.set_params()` and `reset()`. Neither the tunable
  parameters nor the streamer restart — the standard recovery for a frozen
  pipeline — could be reached before (#53).
- `save`, `load` and the preview parameters on `StreamerResource.snapshot()`.
  `delete_snapshot()` existed with nothing able to create what it deleted, and
  `load` is the only way to get an image while the streamer is stopped (#54).
- OCR region cropping: `left`, `top`, `right` and `bottom` on
  `StreamerResource.ocr()`. A full screen takes Tesseract 10-20 s on the Pi,
  a region a fraction of that (#55).
- `StreamerState.applied`, the parameters the running streamer ended up with.
  Comparing it against `params` is the only way to tell whether a change took
  effect — kvmd accepts a value outside the device limits with HTTP 200 and
  then drops it (#52).
- `StreamerLimits.available_resolutions` and a typed `SavedSnapshot` for
  `StreamerState.snapshot.saved`, which was an untyped dict (#52, #54).
- Per-call `timeout` on `StreamerResource.snapshot()`, `set_params()` and
  `reset()`, matching the other subsystems (#66).
- **Breaking:** `ATXState.acts` (`ATXActs`), the per-line busy flags kvmd has
  always sent, is now a declared and required field rather than an untyped
  extra. `busy` is the union of the two; `acts` says whether it is the power
  or the reset line that is occupied. Both ATX plugins emit it
  unconditionally, including the disabled one (#72).
- Models for everything the switch reports: `SwitchSummary`, `SwitchModel`,
  `SwitchUnit`, `SwitchLimits`, `SwitchEdids`, `EDIDInfo`, `SwitchColors`,
  `SwitchLinks`, `SwitchBeacons`, `SwitchAtx` and the pieces they are built
  from. Per-port video and USB link sensors, beacon states and ATX LEDs had no
  representation at all (#42).
- `GPIOModel`, `GPIOScheme`, `GPIOOutputScheme`, `GPIOInputScheme`,
  `GPIOPulse`, `GPIOHardware`, `GPIOView`, `GPIOViewHeader` and `GPIOIOState`
  models. The scheme is where a channel's driver, pin and pulse limits live —
  none of it was reachable before (#41).
- `PiKVM.cookies`, the HTTP client's cookie jar. `login()` leaves kvmd's
  session token there, and putting a saved one back is how a session is
  restored. kvmd stops at the first credential source that is *present*,
  so a token only authenticates a client that sends no `X-KVMD-*` headers —
  in practice an `httpx.AsyncClient` passed in as `http_client` (#34).
- `expire` on `AuthResource.login()`, the session lifetime kvmd accepts and
  the client never sent. `0` asks for an unlimited session; kvmd caps both
  cases at the device-wide limit (#34).
- Form-encoded request bodies (`data`) in `PiKVM.request()` and the resource
  helpers. The HTTP core could only send JSON, which is why `/auth/login`
  could not work at all (#34).

### Changed

- **Breaking:** `PiKVM.ws(stream=...)` and `PiKVMWebSocket(stream=...)` take a
  bool and default to `True`, kvmd's own default. kvmd reads the query
  parameter with its bool validator — it was never a stream index — and counts
  the sessions that asked for video to decide whether the streamer runs. The
  old hardcoded `stream=0` meant an open socket never kept the video pipeline
  alive, which is why `snapshot()` could answer HTTP 503 with a client
  connected. Pass `stream=False` for a client that only reads events (#79).
- **Breaking:** a handshake kvmd refuses raises `AuthError` (401/403) or
  `APIError`, carrying the status and kvmd's error block, instead of a blanket
  `WebSocketError` with no detail. Both transports share one status table, so
  409 is a `BusyError` and 503 an `UnavailableError` whichever one reported
  it. `WebSocketError` now means a socket that never opened or that broke —
  DNS, TLS, a timeout, or a server that does not speak WebSocket (#59).
- **Breaking:** `events()` raises `WebSocketError` when the connection breaks
  instead of ending the iteration silently. A clean close from either side
  still ends it quietly; the old blanket `except ConnectionClosed` caught only
  the abnormal ones, since websockets ends the iteration itself on a clean
  close — so every dropped connection looked like "kvmd has nothing more to
  say" (#59).
- **Breaking:** a redirected WebSocket handshake raises `RedirectError`
  instead of being followed. *websockets* follows up to ten redirects on its
  own, resending the credential headers to each target; the REST client has
  refused to do that since #67, and the socket carries the same password.
  `ws()` inherits the client's `follow_redirects` (#59).
- **Breaking:** `HIDKeyboard` and `HIDMouse` replace `connected` with the
  fields kvmd actually sends: `online`, `outputs` and — on the keyboard —
  `leds`. `HIDState` gains `enabled` and `jiggler`, and keeps the nullable
  top-level `connected` (#36).
- **Breaking:** `StreamerResource.snapshot()` returns a `SnapshotImage`
  instead of bare bytes. The JPEG is in `.data`, and `.online` says whether it
  is the host screen or the "NO LIVE VIDEO" placeholder — kvmd reports that
  only in the response headers, which the old return type discarded (#54).
- **Breaking:** the optional halves of `StreamerState` are optional in the
  model too. kvmd builds `params` and `limits` from what the device supports,
  so `quality`, `resolution`, `h264_bitrate` and `h264_gop` are absent on
  hardware without them, as is `streamer.h264`; requiring them made
  `get_state()` raise on any device without H.264 (#52).
- **Breaking:** every mutating `ATXResource` call now defaults to
  `wait=False`, matching kvmd. Waiting was the client's own invention and held
  the HTTP request for the length of the action — a long power click holds the
  button for 5.5 s and kvmd waits another second after it, against a 10 s
  default timeout. Pass `wait=True` (with a wider `timeout`) where confirmation
  matters (#73).
- **Breaking:** `SwitchState` follows the real payload — `model`, `summary`,
  `edids`, `colors`, `video`, `usb`, `beacons`, `atx` — instead of the flat
  `{active, ports}` no kvmd ever emitted. `EDID` now carries `name`, `data`
  and `parsed`; its `id` and `description` fields never existed (#42, #43).
- **Breaking:** `SwitchResource.set_active()` takes a port number rather than
  a name — kvmd validates it as a float, so the documented `"port1"` was
  always a 400. `set_colors()` covers all five roles instead of only
  `beacon`, and `set_beacon()` raises `ValueError` unless exactly one of
  `port`/`uplink`/`downlink` is given, which is what kvmd requires; the
  documented target-less "turn all beacons off" call does not exist. Calls
  kvmd would accept and silently ignore — `set_colors()` with no role,
  `change_edid()` with nothing to change — raise `ConfigurationError` too
  (#56).
- **Breaking:** the EDID methods now match kvmd. `create_edid(name, data)`
  sends query parameters and returns the id kvmd generated; `change_edid()`
  edits a stored EDID by id instead of pretending to assign one to a port
  (that is `set_port_params(edid_id=...)`); `remove_edid()` sends `id`; and
  `get_edids()` returns the catalogue out of the switch state, since the
  `GET /switch/edids` endpoint it used to call does not exist (#43).
- **Breaking:** `GPIOState` follows the two-level shape kvmd sends —
  `model {scheme, view}` and `state {inputs, outputs}` — instead of a flat
  `inputs`/`outputs` no kvmd ever emitted. `state.inputs` and `state.outputs`
  are also exposed as `inputs`/`outputs` properties, so code that only reads
  channel states keeps working (#41).
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
- **Breaking:** `AuthResource.login()` returns the session token as a `str`
  instead of the response envelope, which was an empty dict on every call.
  kvmd sends the token only in a `Set-Cookie` header, so there was no way to
  get hold of it. The string is empty when kvmd runs with authentication
  disabled and hands out no session (#34).
- **Breaking:** `AuthResource.check()` and `logout()` return `None` instead of
  the same empty envelope. Whether kvmd answered at all is the entire result;
  a refusal raises `AuthError` (#34).
- `AuthResource.logout()` is documented as what kvmd actually does: it looks up
  the token's owner and closes **every** session that user has, so logging out
  a token a script created also signs the same account out of the web UI (#34).
- **Breaking:** credentials kvmd's validators reject — a user name that fails
  its regex, a password with non-printable characters — now raise `AuthError`
  rather than a bare `APIError`. kvmd reports them as HTTP 400 `ValidatorError`
  before it looks anything up, but from the caller's side the login still
  failed on the credentials. `AuthError` derives from `APIError`, so existing
  handlers keep working (#34).

### Removed

- **Breaking:** the `wait` parameter of `HIDResource.send_shortcut`.
  kvmd never reads it — the inter-event delay is hardcoded server-side
  (50 ms) — so the parameter never had any effect (#31).
- **Breaking:** the `HIDKeymap` model. Its single `name` field described
  nothing kvmd emits, and no method ever returned it; `HIDKeymaps` replaces
  it (#75).

### Fixed

- The WebSocket HID methods let `websockets`' own `ConnectionClosed` escape to
  the caller when the socket was already dead, outside the documented
  `PiKVMError` hierarchy. `__aenter__` could likewise let a bare `ValueError`
  through for a URI `websockets` rejects itself. Both now raise
  `WebSocketError` (#59).
- `PiKVM.request()` let `httpx.TooManyRedirects` escape when the client was
  created with `follow_redirects=True`. httpx derives it from `RequestError`
  rather than `TransportError`, so none of the clauses caught it; it now
  arrives as `RedirectError`.
- The WebSocket guide described a handshake that does not happen. kvmd does
  not send "a full state bundle" first: `loop` carries the protocol version,
  then each subsystem sends its state in no guaranteed order, and later
  updates can be partial — `info` only ever sends one key at a time. The event
  types are now documented from a capture of all twelve (#80).
- The mouse documentation read as if the coordinates were pixels.
  `send_mouse_move()` works in kvmd's normalized space, -32768 to 32767, so
  `send_mouse_move(500, 300)` is the middle of the screen and not a pixel
  position; wheel and relative deltas are steps in -127 to 127. Both ranges
  are clamped by kvmd rather than rejected, so the old reading failed
  silently. The scroll example also had its direction backwards (#80).
- `AuthResource.login()` failed with HTTP 400 against every real device. kvmd
  reads the credentials with aiohttp's form parser, which finds nothing in the
  JSON body the client sent, so it validated an empty user name and refused
  the request. `logout()` was broken the same way: kvmd identifies the session
  to drop by the `auth_token` cookie, which the client never sent, and answered
  HTTP 400 for every call. Both are verified against kvmd 4.186 (#34).
- `HIDResource.get_state()` raised `ResponseError` against every real device:
  `HIDKeyboard.connected` and `HIDMouse.connected` were required, but no kvmd
  HID backend nests `connected` under `keyboard` or `mouse` — it exists only
  at the top level. The mocked test passed because its payload was
  hand-written; the models now follow a capture from kvmd 4.186 (#36).
- `StreamerResource.get_state()` raised on a device without H.264 or without
  an adjustable encoder, and hid the resolution data on capture hardware that
  has it. The captured fixture covers one flavour only, so the conditional
  blocks are exercised by deriving the other shapes from it (#52).
- `SwitchResource.get_state()` raised on every real device — the model was a
  flat `{active, ports}` shape no kvmd emits (#42).
- The captured switch fixture identified the monitor its EDID was learned
  from — manufacturer, product id, serial and model name, in the decoded block
  and again inside the raw blob. The capture tool now replaces the decoded
  fields by key path and rewrites the blob itself, recomputing the checksums
  so the result is still a valid EDID, and the committed fixtures were
  regenerated through it (#42).
- `GPIOResource.get_state()` raised on every real device: the model expected
  top-level `inputs`/`outputs`, while kvmd nests them under `state` alongside
  a `model` block. The mocked test encoded the flat shape and passed (#41).
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
