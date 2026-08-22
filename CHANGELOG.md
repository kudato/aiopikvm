# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added

- `binary` option on `PiKVM.ws()` and `PiKVMWebSocket`: HID input goes out as
  kvmd's binary operations (`1` key, `2` mouse button, `3` absolute move, `5`
  wheel) instead of JSON events. Both reach the same handlers and the same
  validators; the binary frames are a few bytes each instead of a JSON object
  to parse, which is why kvmd's own web UI uses them for every keystroke and
  mouse move. Off by default: JSON is what the client has always sent, and is
  the encoding a packet capture can be read in (#81).
- `PiKVMWebSocket.version`, the kvmd version from the `loop` event as a
  comparable `KvmdVersion` tuple. It is the only version signal the socket
  carries, and the client ignored it (#82).
- `PiKVMWebSocket.states()`, the typed view of the event stream: each event
  merged into what the same subsystem said before and validated against the
  same model its REST endpoint returns, yielded as a `DeviceState` snapshot per
  event that changed something. The merge is what makes it work at all — kvmd
  sends a subsystem in full once and then only the parts that change, so a
  later event validated on its own fails for want of the rest of the model.
  `info` is merged the same way and is an `InfoState` (#61).
- `PiKVMWebSocket.send_mouse_relative()`, the WebSocket half of relative mouse
  motion — the client had only the HTTP fallback. kvmd drops a relative event
  while the mouse is in its absolute mode and drops an absolute one while it is
  relative, in both cases silently, so `HIDState.mouse.absolute` is what says
  which of the two will do anything (#60).
- `PiKVMWebSocket.send_mouse_relative_batch()` and `send_mouse_wheel_batch()`
  put several steps in one frame, the way kvmd's own web UI sends a burst of
  movement. `squash` asks kvmd to add consecutive steps together where they fit
  into one report — fewer reports for the host, and nothing at all sent when a
  squashed batch adds up to zero (#60).
- `finish` on `HIDResource.send_key()` and `PiKVMWebSocket.send_key()`: kvmd
  queues the release itself, in the same handler call that took the press, so
  no second request is owed — on the CH9329 backend the two are separate
  commands in one queue, which narrows the window rather than closing it. It
  rides a press and nothing else, and kvmd exempts the modifiers and
  `PrintScreen`: those are pressed and stay down with no error to say so
  (#74).
- `aiopikvm.resources.hid.KEY_NAMES`, the 126 key names kvmd accepts — the
  keys of its `WEB_TO_EVDEV` table, matched case-sensitively. Only one of the
  two transports says when a name is wrong: `send_key()` and
  `send_shortcut()` raise `APIError` with HTTP 400, while the WebSocket drops
  the frame inside kvmd's handler and answers nothing at all. No endpoint
  exposes the table, so the catalogue is a copy read off a device running
  kvmd 4.206 and pinned to that capture by a contract test; nothing in the
  client enforces it, since a later kvmd may know more names (#77).
- `aiopikvm.resources.redfish.RESET_TYPES`, the six `ResetType` values kvmd
  accepts. The DMTF schema defines more, and kvmd refuses every one of them
  before taking any action (#78).
- Literal types for the short vocabularies kvmd takes as strings:
  `KeyboardOutput`, `MouseOutput` and `MouseButton` in
  `aiopikvm.resources.hid` — the last shared by the REST call and the
  WebSocket one — `Compression` in `aiopikvm.resources.msd`, `ATXAction` and
  `ATXButton` in `aiopikvm.resources.switch`, and `ResetType` in
  `aiopikvm.resources.redfish`, where `RESET_TYPES` is now read off the type
  instead of written out a second time. A misspelled output or button used to
  reach the device and come back as HTTP 400, or, over the WebSocket, as
  nothing at all; it is a type error now. `get_args()` gives the values for a
  name that only exists at runtime (#68).
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
- `MSDResource.upload_remote_progress()`, an async iterator over the NDJSON
  stream `POST /msd/write_remote` answers with. kvmd sends one record before
  the first byte, one about every second while the download runs and one when
  it ends; nothing in the client could read them, so a transfer that takes
  hours reported nothing at all (#40).
- `name`, `prefix`, `insecure` and `remove_incomplete` on
  `MSDResource.upload_remote()`. kvmd accepts all four and the client sent
  none of them: without `name` the image is stored under whatever the remote
  calls it, and without `remove_incomplete` a failed download leaves an
  incomplete image occupying the name, which is then refused on the retry
  (#40).
- `SystemResource.get_state()` and the `Info*` models behind it. `/api/info`
  was the one subsystem with no types at all, and it carries what a dashboard
  polls: CPU load and temperature, throttling flags, memory, fan state, the
  kvmd and streamer versions. The models follow the per-submanager shape —
  the one `legacy=False` returns and the one the WebSocket `info` events
  carry — because the legacy shape has no submanager behind its `hw` and no
  single model could describe both. Every attribute is optional, since the
  same model is what `PiKVMWebSocket.states()` fills in one event at a time.
  `meta` and `fan.state` stay dictionaries: the first is a YAML file the
  device's owner writes and kvmd reads one key out of, the second belongs to
  the `kvmd-fan` daemon and arrives over its own socket, so neither shape is
  kvmd's to promise (#71).
- `legacy` on `SystemResource.get_info()`, and `InfoField` for the categories
  it takes. kvmd assembles `/api/info` from eight submanagers and, unless
  `legacy=0` is asked for, rearranges them into the shape its older API had:
  a synthetic `hw` holding `health` and the `platform` block lifted out of
  `system`, `health` removed from the default set, and `system` dropped
  altogether unless it was named as well — even though `hw` is built out of
  it. That rearrangement was unreachable and undocumented; `legacy=False`
  now asks for the per-submanager shape the WebSocket `info` events carry.
  `legacy=True` matches kvmd's own default, so a plain call sends no `legacy`
  param and the request is unchanged (#46).

### Changed

- **Breaking:** `DeviceState.info` is an `InfoState | None` instead of a
  `dict[str, Any]`. It defaulted to an empty dictionary and now defaults to
  `None`, so a snapshot taken before the first `info` event says so rather
  than looking like a device with nothing to report (#71).
- **Breaking:** kvmd 4.206 is now the declared minimum. The client had never
  said which versions it supports, so every method was implicitly promised
  against every kvmd ever shipped, and several were not: an event carrying a
  flag an older kvmd does not read is dropped inside its handler with no
  answer of any kind, which looks exactly like a call that landed. Nothing
  inspects the device's version or refuses an older one — the socket has no
  version to check until kvmd's first `loop` event arrives, and refusing on
  that basis would reject calls that work. The floor is stated in the README,
  the installation guide and `pyproject.toml`, and the documentation no longer
  describes the behaviour of versions below it (#114).
- Every fixture is re-captured from kvmd 4.206 (was 4.186), and the capture
  tool now reproduces the corpus rather than whatever the device happened to
  be doing: it asks for video so the streamer is running, pings so a `pong` is
  recorded, listens long enough for a partial event, and refuses to write a
  capture missing any of the three. The previous corpus had all three by
  luck, and a re-run silently dropped them — six tests kept passing against a
  corpus that no longer proved what they read (#114).
- **Breaking:** the parameters those literal types cover are no longer `str`:
  `hid.set_params()`, `hid.send_mouse_button()`, `ws.send_mouse_button()`,
  `switch.atx_power()`, `switch.atx_click()`, `redfish.reset()` and
  `system.get_info()`.
  (`msd.download(compress=…)` is typed the same way but has never shipped, so
  nothing breaks there.) Nothing changes on the wire — the client still sends
  whatever it is handed — but a caller passing a variable inferred as `str`
  now needs it annotated with the type, or a `cast`, which fails a strict
  build that used to pass. The types stay out of the response models, where
  one would turn a value from a kvmd this release has not seen into a
  `ResponseError`. Key names are left as `str` for the same reason they are a
  runtime set: there are 126 of them, and a key is usually computed rather
  than written down (#68).
- **Breaking:** `HIDResource.send_shortcut()` raises `ConfigurationError`
  instead of `ValueError` when called with no keys, and now refuses a key
  that is empty or holds a comma or any whitespace. kvmd takes the shortcut
  as one string, strips it, splits it on commas, spaces and tabs and drops
  what falls out empty — so `send_shortcut("ControlLeft", "")` used to press
  one key, answer 200 and say nothing about the other, which is the kind of
  silent miss `KEY_NAMES` exists to prevent. `ConfigurationError` is what the
  other resources already raise for unusable arguments (#77).
- `HIDResource.set_connected()` now says where it does anything. kvmd 4.206
  implements it only in the MCU-based backends (`hid.type` of `serial` or
  `spi`); under `otg`, `ch9329` and `bt` the call reaches a base
  implementation that discards its argument, so it answers 200 and nothing
  happens. The docs presented it as a universal "disconnect the HID". The
  signal that tells the two apart is `HIDState.connected`, and it points one
  way only: a `bool` there is a backend that implements the call, while a
  `None` is also what an MCU backend reports before its microcontroller has
  sent a status word. `reset()`, which every backend does override, is
  documented per backend — it releases held keys under `otg` and `bt`, resets
  the microcontroller under `serial` and `spi`, and does nothing at all under
  `ch9329`, whose reset request is commented out (#76).
- **Breaking:** `PiKVMWebSocket.ping()` waits for the answer and returns the
  round trip in seconds, raising `WebSocketError` when none arrives within
  `timeout`. It used to send the frame and return, which made it useless as
  the liveness check it looks like: a socket whose kvmd had stopped
  dispatching answered it just as happily as a healthy one. When `events()` is
  being iterated in another task, that iteration hands the pong over;
  otherwise `ping()` reads the socket itself and keeps the events it finds on
  the way for the next `events()` call (#82).
- **Breaking:** `PiKVMWebSocket.events()` no longer parses binary frames as
  JSON. kvmd sends exactly one thing on that channel — operation `255`, the
  answer to a binary ping — which `ping()` consumes; anything else there is
  logged and dropped, as is a text frame whose JSON is not an event object.
  kvmd sends its events as text, so nothing it broadcasts is affected (#81).
- **Breaking:** `PiKVM.aclose()` is final, and leaves the client closed
  whoever owns the underlying HTTP client. With an external `http_client` the
  reference was kept, so every resource went on working after the block that
  owned them ended — through an `httpx.AsyncClient` the caller was free to
  have closed by then. Resources, `base_url`, `cookies`, `request()`,
  `stream()` and `ws()` now all raise afterwards, and the message says the
  client was closed rather than never entered. Closing twice is still a no-op
  (#70).
- **Breaking:** entering a `PiKVM` twice, or entering one again after closing
  it, raises `ConfigurationError`. Re-entry silently built a second connection
  pool under the same object, rereading the credentials as they stood at that
  moment, and a nested `async with` closed the connection the outer block was
  still using. `httpx.AsyncClient` refuses both, and wrapping one should not
  change the rules (#70).
- **Breaking:** `MSDResource.upload()` returns `MSDUpload` instead of `None`.
  kvmd answers a write with `{"image": {"name", "size", "written"}}`, and the
  name in it is the one the image was actually stored under — a `prefix` is
  joined on server-side and the whole thing goes through kvmd's file-name
  validator, so the name to pass to `set_params()` or `remove()` afterwards
  was unobtainable (#39).
- **Breaking:** `timeout` on `MSDResource.upload_remote()` is this client's
  request timeout, as everywhere else in the library, and defaults to having
  the read timeout disabled — the response stays open for the length of the
  download. kvmd's own `timeout` query parameter is now `connect_timeout`,
  which is what it is: how long kvmd waits to *connect* to the URL. It never
  bounded the download, on which kvmd puts no total limit at all (#40).
- **Breaking:** `RedfishResource.reset()` and `update_system()` return `None`.
  kvmd answers both with HTTP 204 and an empty body, so the documented `dict`
  was unreachable: `_redfish_request()` called `.json()` on nothing and every
  call raised `APIError("Invalid JSON response")` — after the action had
  already been dispatched, which made a performed reset look like a failed
  one. A reset is asynchronous besides, so read the outcome from
  `get_system()["PowerState"]` or from `atx.get_state()` (#44).
- A Redfish document that is not a JSON object raises `ResponseError`
  instead of being handed to the caller untyped, and a non-JSON body reports
  the path and what arrived instead of the generic
  `APIError("Invalid JSON response")`. `ResponseError` derives from
  `APIError`, so existing handlers keep catching both (#44).
- **Breaking:** Redfish system ids are strings. `system_id` is typed `str` and
  defaults to `"0"`, and `reset()` takes it as a second parameter instead of
  hardcoding `Systems/0`. kvmd validates the id as the literal `"0"` or
  `"SwitchPort<N>"`, so the old `int` made every value but `0` a guaranteed
  HTTP 400 and left switch-port power control unreachable (#57).
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

- `SystemResource.get_info()` documented a field list that was wrong in both
  directions and a default that never existed. It named `hw` as a category
  like any other and left out `health`, `node` and `uptime` entirely, and it
  claimed a bare call "returns all categories" — kvmd removes `health` from
  the default set under the legacy shape, so the one category a health check
  wants is the one a bare call does not get. The guide said the same. Its
  tests could not have caught it: every `/api/info` payload there was
  hand-written, so they asserted the shape the author believed in. They read
  four real captures now, one per shape kvmd can answer with (#46).
- A link to an anchor that does not exist now fails the docs build. mkdocs
  checks the file half of a Markdown link by default and says nothing about
  the fragment, so a link into a heading that had been renamed reached the
  published page and went nowhere. `validation.anchors` is what checks it,
  and under `mkdocs build --strict` — which CI runs — the warning is a
  failure (#114).
- The API reference printed its cross-references as their own source text.
  The docstrings used reStructuredText roles — ``:meth:`PiKVM.request` `` and
  the like — and nothing renders reST here: mkdocstrings is configured for
  Google style, so each one reached the page verbatim. There were 124 in the
  source, of which 22 sit in docstrings the site never renders, so about a
  hundred were actually on show. They read correctly in an editor and in
  `help()`, which is why it went unnoticed for so long. 120 are now
  mkdocstrings cross-references, checked one at a time against the anchors the
  site actually has, and the two `:pymethod:` among them — never a Sphinx role
  at all — are gone with them. The remaining four had no target to reach:
  `httpx.AsyncClient` twice, `httpx.Cookies.set` and the private
  `BaseResource._request` became plain code spans rather than links to
  nowhere, since no inventory for httpx is configured and the base resource is
  not published. `PiKVM.cookies` *is* now published, since five references
  point at it. Four reST literal-block markers (``Usage::``) went the same way
  as the roles, for the same reason. Four type-alias summaries were reworded
  so the sentence ends on the link rather than wrapping around it (#111).
- The `Returns:` table on seven published members listed one row per source
  line, repeating the return type down the column and cutting the sentence
  between them — `RedfishResource.get_system` rendered as four rows reading
  "System resource document, including `PowerState` and the" / "`ResetType`
  values" / "`reset()`" / "accepts for it." Griffe's Google parser treats each
  unindented line of a `Returns:` body as a separate returned value, and no
  docstring in this package ever meant that: all fifteen multi-line bodies are
  one value with its prose wrapped. `returns_multiple_items` is now off, which
  says so once instead of reindenting fifteen docstrings (#111).
- CI never built the documentation, so nothing ran the one check that catches
  a cross-reference pointing at something the site does not publish —
  mkdocs-autorefs warns, and `mkdocs build --strict` fails on the warning. The
  deploy workflow runs `mkdocs gh-deploy --force`, which is not strict. CI now
  builds the docs strictly (#111).
- The HID guide had the mouse wheel backwards: it labelled
  `send_mouse_wheel(0, -5)` "Scroll up" where the WebSocket guide labels the
  same call "scroll down". Both reach the same kvmd handler and the same HID
  report, so at most one could be right, and kvmd's own source decides it: a
  browser reports a scroll-down gesture as a positive `deltaY` and kvmd's web
  UI negates it, so the gesture reaches the device as `delta_y = -5`. The HID
  guide now agrees, and gained the step range and the negation that only the
  WebSocket page carried. The direction was read off kvmd rather than off a
  target's screen, and the one thing that reading does not settle is which
  way a positive `delta_x` pans — the guide says so instead of guessing.
  Alongside it, both `send_mouse_wheel()` docstrings gained what `delta_x`
  needs (a backend with a horizontal wheel: only `otg`, with its
  `horizontal_wheel` option on, which is the default) and what `ch9329` does
  to a step (keeps the sign, drops the size, sends one detent) (#112).
- `MSDResource.upload_remote()` raised `APIError("Invalid JSON response")` on
  every call, successful ones included. `POST /msd/write_remote` answers HTTP
  200 with `application/x-ndjson` — one envelope per line, never fewer than
  two — and the client called `.json()` on the concatenation. The download had
  usually finished by then, so the one call that could not fail was reported
  as the one that did (#40).
- A remote download that fails is now raised as the reason it failed. kvmd has
  already sent HTTP 200 by the time it knows, so it writes the failure as one
  last record and lets the exception escape its handler — the chunked body
  never gets its terminating chunk. Reading record by record surfaces
  `ClientPayloadError` or whatever else kvmd names, instead of httpx's
  "peer closed connection without sending complete message body" (#40).
- The mocked upload tests answered `{"ok": true, "result": {}}`, which kvmd
  never sends, so nothing noticed that the write info was being thrown away.
  Both endpoints are now pinned to a capture recorded off a real device, the
  NDJSON stream and its broken connection included (#39, #40).
- Documented what `prefix` does on a directory that is not there yet: kvmd
  creates the image's `.incomplete` marker before it creates the directory, so
  the call fails on an unhandled `FileNotFoundError` — a plain-text HTTP 500
  with no error block for a message to come from. The prefix has to already
  exist (#39).
- Documented that `remove()` returns before kvmd's storage listing catches up,
  so re-uploading the same name immediately afterwards is refused as already
  existing (#39).
- `RedfishResource.update_system()` claimed to apply the attributes and return
  the updated document. kvmd's handler is a stub that answers 204, ignores the
  body and does not look at the system id; it exists so that BMC tooling which
  PATCHes a system does not fail. Nothing it is given reaches the device (#44).
- The Redfish guide advertised `ResetType` `GracefulRestart`, which kvmd
  refuses with HTTP 400 — the documented example could never have worked. The
  guide now lists the six values kvmd accepts, notes that they are matched
  case-sensitively, and spells out what each one does: they press the front
  panel's power or reset switch, and all but `PushPowerButton` are conditional
  on the host's current power state, so the default `ForceRestart` is a reset
  click that does nothing at all against a host kvmd believes to be off — a
  state it reads from the power LED. It also warns that the `enabled` check
  which makes a reset harmless on a device with `atx.type = disabled` guards
  the `"0"` branch only, so a `"SwitchPort<N>"` reset acts on the port
  regardless, records that the `SetDefaultBootOrder` action every system
  document advertises is answered by a plain-text 404, that a reset does not
  bounds-check a switch port — a nonexistent one answers 204 and does nothing,
  where `get_system()` answers 400 — and that `Members` holds
  `{"@odata.id": ...}` links rather than bare ids (#78).
- The Prometheus guide showed `# HELP` lines the exporter does not emit, and
  documented none of its limits: the export is cached server-side for 5 s, it
  covers only ATX, GPIO, health and fan, it exports numbers only, and an
  upstream kvmd bug fills `pikvm_gpio_*_online_*` from the channel `state`, so
  that metric does not report online-ness at all (#78).
- The WebSocket HID methods let `websockets`' own `ConnectionClosed` escape to
  the caller when the socket was already dead, outside the documented
  `PiKVMError` hierarchy. `__aenter__` could likewise let a bare `ValueError`
  through for a URI `websockets` rejects itself. Both now raise
  `WebSocketError` (#59).
- `PiKVM.request()` and `PiKVM.stream()` let `httpx.TooManyRedirects` escape
  when the client was created with `follow_redirects=True`. httpx derives it
  from `RequestError` rather than `TransportError`, so none of the clauses
  caught it; it now arrives as `RedirectError` with `status_code` `0`, since
  the client gave up across several responses rather than refusing one (#59).
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
  HTTP 400 for every call. Both are verified against kvmd 4.206 (#34).
- `HIDResource.get_state()` raised `ResponseError` against every real device:
  `HIDKeyboard.connected` and `HIDMouse.connected` were required, but no kvmd
  HID backend nests `connected` under `keyboard` or `mouse` — it exists only
  at the top level. The mocked test passed because its payload was
  hand-written; the models now follow a capture from kvmd 4.206 (#36).
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
