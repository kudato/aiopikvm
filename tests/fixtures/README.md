# Contract fixtures

Real responses captured from a PiKVM device, used by the test suite instead of
hand-written payloads. Hand-written mocks are how this project shipped five
subsystems whose `get_state()` could never parse a real response: the mock
encoded a shape kvmd does not produce, and the tests passed anyway.

| | |
|---|---|
| Device | PiKVM v3 (Raspberry Pi Compute Module 4) |
| kvmd | 4.206 |
| Streamer | ustreamer 6.62 |

Exact metadata lives in [`data/_manifest.json`](data/_manifest.json), which also
records the request (method, path, query params) and the response status and
content type behind every file.

## Using them

```python
from tests.fixtures import load_json, load_result, load_text

mock_api.get("/api/atx").mock(return_value=httpx.Response(200, json=load_json("atx")))
state = ATXState.model_validate(load_result("atx"))
metrics = load_text("prometheus_metrics")
```

`load_json` returns the full body including the `{"ok": ..., "result": ...}`
envelope, `load_result` returns the unwrapped `result`, `load_text` returns
plain-text captures verbatim, and `load_jsonl` parses the JSON Lines scenarios.

## Refreshing them

```bash
PIKVM_URL=https://pikvm.local PIKVM_PASSWD=secret \
    uv run python -m tests.fixtures.capture
```

The tool requests only read-only endpoints — it never changes device state — and
rewrites `data/` plus the `captures` and `device` sections of the manifest.
`PIKVM_USER` (default `admin`), `PIKVM_TOTP` and `PIKVM_SCRUB` are optional; see
the module docstring.

## Sanitization

Captures come from someone's actual device, so the tool redacts before writing:

- the hardware serial and the `host` fields of `/api/info` and Redfish, by key
  path, replaced with `0000000000000000` / `pikvm`;
- the target host name (including its longer labels), the user name and the
  password, anywhere they appear in the text;
- IPv4 and MAC addresses, plus the client-address field of kvmd's
  `aiohttp.access` log lines, replaced with `192.0.2.10` (RFC 5737) and
  `00:00:00:00:00:00`;
- the monitor an EDID was learned from: the decoded `parsed` block by key path,
  and the raw blob by rewriting it — manufacturer, product id, serial and
  monitor name are replaced and the checksums recomputed, so the fixture stays
  a valid EDID that identifies nothing;
- anything else listed in `PIKVM_SCRUB`, for device-specific values the rules
  above cannot know about — switch port names, an internal DNS suffix. It is a
  literal text replacement, so it cannot reach a value that only appears
  hex-encoded.

A final guard refuses to write a file that still contains a redacted string or
an address that is not the placeholder, so a leak fails the capture instead of
silently landing in git.

Two endpoints are deliberately **not** captured: `/api/streamer/snapshot` and
its OCR variant return whatever is on the attached host's screen.

`auth_roundtrip.json` was recorded by hand (with `httpx`, following the steps
listed in the file) rather than by the capture tool, which stays read-only —
the sequence logs in and out and would invalidate a session.

The four `msd_*` scenarios were recorded the same way, and needed the device
reconfigured for the duration: `otg.devices.msd` does not start on the capture
device, so `/api/msd` reports `drive: null, storage: null` and shows nothing of
the online shape. `start: false` is the whole of that switch on kvmd 4.206 —
there is no `enabled` beside it, and the `enabled` `/api/msd` reports is about
the MSD plugin rather than the gadget. With `start` turned on and kvmd
restarted, they cover a drive holding an image (`msd_image`), an empty drive
with an image in storage (`msd_online`), and both transfer counters mid-flight
(`msd_uploading`, `msd_downloading`, sampled while a temporary image was being
written and read back). The temporary images were removed and the OTG profile
restored afterwards. Reproducing them means changing somebody's device, so ask
first — and note that toggling the MSD recreates the USB gadget, which briefly
disconnects the keyboard, mouse and audio the target host sees.

These four are the corpus's last recordings from kvmd 4.186. Being outside the
capture tool's reach is not what makes them so: the tool produces exactly one
of the thirteen scenarios, `ws_events`, and of the twelve it does not, the
eight besides these four have each been re-recorded or re-verified against
4.206 by hand. Nobody has been back to these. Nothing in them has drifted that
anyone has shown; they are simply older than the row at the top of this file.

`msd_write.json` was recorded in the same reconfigured state, and needed one
more thing the storage does not normally have: an `isos` directory, so that
both outcomes of the `prefix` parameter could be recorded side by side. It
covers the write info `POST /api/msd/write` answers with, the whole NDJSON
body of a `POST /api/msd/write_remote` that succeeded, the body of one whose
origin died mid-transfer — where kvmd appends its error record and then breaks
the connection without closing the body, which the `stream_broken` field
records — and every refusal kvmd makes before it starts streaming.

The remote downloads went to a throttled HTTP server running on the device's
own loopback and serving generated zeros, so nothing was fetched from the
internet and nothing in the file needed sanitizing: every address it carries,
including the ones kvmd echoes back out of a failed connection, is one the
device resolved on its own loopback. Every image was removed afterwards, the
`isos` directory taken out again and the OTG profile restored. Re-recording it
means writing to somebody's storage — the eMMC on a v3 board — so ask first.

`ws_handshake.json` was recorded by hand too, with the `websockets` client
opening `GET /api/ws` once per refusal — a bad `stream` value, a wrong
password, an unknown user, and no credentials at all. The capture tool cannot
produce it: it holds one working socket, and these are the four ways of not
getting one. Unlike the other scenarios it records HTTP responses rather than
kvmd envelopes, since the upgrade is refused before the socket exists, so each
step carries the `status`, `reason_phrase` and `content_type` a client sees.

`hid_keys.json` is the odd one out: it holds no response at all, but kvmd's
own `WEB_TO_EVDEV` table — every key name its validator accepts. No endpoint
exposes it, so it was read off the device itself rather than off the wire,
with the one-liner recorded in the manifest entry. It is what keeps
`aiopikvm.resources.hid.KEY_NAMES` honest: a copy of a table nothing can
fetch at runtime is exactly the kind of thing that drifts unnoticed. Unlike
the other hand-recorded files it carries no `description` of its own — it is
kept as exactly what that command prints, so re-reading the table on a newer
device diffs clean and any difference is a real one. What happens to a name
that is *not* in it is recorded on the other side, in `ws_binary.json` below.

`ws_binary.json` is the other half of that socket: kvmd's binary channel, where
the first byte of a frame is an operation number rather than JSON. Most of what
it holds this client builds, down to the byte the tests compare against — the
refused key name included, since nothing in the client checks a name against
that table. Three frames it cannot build: an operation it does not implement,
and two frames truncated below the width its packers emit, which are here to
record the shape of a bug rather than anything a caller can reach. Each input
frame is recorded with `/api/hid/inactivity` read before and after — kvmd
resets that counter from inside the handler and it counts seconds from there,
so a counter that came down is the device confirming it decoded the frame, and
one that did not come down is it dropping one its validators refused. Neither
side is a fixed number: an accepted frame reads back as 0 or 1 and a refused
one either sits where it was or ticks on, depending on where the second
boundary fell between the two reads. The verdict is the drop, not the value.
Alongside the input frames it holds both ping exchanges (the JSON `pong` event
and binary op `255`), and an operation kvmd has no handler for, which it
answers with nothing at all.

The mouse on the recording device was in its absolute mode, which is why the
relative frames are recorded as accepted rather than as movement: kvmd decodes
them and resets the counter, and its mouse device then drops the report for
being in the wrong mode. Recording them as movement would mean switching
`mouse_output` on somebody's device, which recreates the USB gadget.

Input frames reach the HID, and this one was recorded with the USB gadget
attached to a host — `keyboard.online` and `mouse.online` both `true` — so most
of what kvmd accepted reached the machine behind it too, and some of it did
something there. `ControlLeft` is one of the nine keys kvmd exempts from the
release the finish bit asks for, so from `key_press_finish` onward it stays down
until the recording sends a plain release for it at the end. Inside that window
`key_press_finish_ordinary` presses `KeyA`, which the host reads as Ctrl+A
rather than as a character, and `mouse_wheel_batch_squashed` scrolls two steps
with the modifier still held, which much of what a host runs takes for a zoom.
`mouse_move` puts the pointer at the centre of the screen: an absolute move is
delivered in the mode this device was in, and nudging the pointer is exactly
what the jiggler exists to do. What reaches the host and does nothing there: the
`ControlLeft` press and release that open the run, the wheel of zero, and a
middle-button release with no press before it. What never leaves the device: the
two relative frames, which kvmd decodes and counts and its mouse device then
drops for being in an absolute mode, and the three frames kvmd refuses outright.
The socket was opened with `stream=0`, so the video pipeline was untouched, and
the jiggler was enabled but idle throughout — it moves the same counter these
steps are judged by, so a run with it active could not tell an accepted frame
from a jiggle. Nothing on the device needs undoing, but re-recording it sends
input to somebody's device and to whatever is attached to it, so ask first.

`media_stream.json` is the live-video scenario, and the only one with a
recorder of its own next to it — [`record_media.py`](record_media.py), run the
same way the capture tool is:

```bash
PIKVM_URL=https://pikvm.local PIKVM_PASSWD=secret \
    uv run python -m tests.fixtures.record_media
```

It is read-only, but it is not something the capture tool could ever produce:
half of what matters here is a refusal, a WebSocket, or a stream that never
ends. It holds ustreamer's own state with nobody watching (an nginx **502**, no
kvmd envelope in sight), with a session watching, and with a named client in
`clients_stat`; the MJPEG part headers plain, with `extra_headers`, with
`zero_data`, and under the two browser workaround flags the client does not
offer; ustreamer's own **404**; the media daemon's state; and both media
sockets, pure and regular, with the two refused video formats.

No frame payload is in it. An MJPEG part and an H.264 frame are a picture of
whatever is on the attached host's screen, the same reason
`/api/streamer/snapshot` is not captured — only each frame's length and first
few bytes are recorded, which is what the framing tests need. Re-recording it
holds an event socket open for about a minute and reads video; nothing needs
undoing afterwards, but it is somebody's device, so ask first.

`janus_session.json` is the WebRTC scenario, with the second recorder of its
own — [`record_janus.py`](record_janus.py), which needs the `webrtc` extra
(`uv sync --all-groups`) because building an answer Janus will accept means a
real peer connection:

```bash
PIKVM_URL=https://pikvm.local PIKVM_PASSWD=secret \
    uv run python -m tests.fixtures.record_janus
```

It walks one whole session against `/janus/ws` — create, attach, features,
watch, answer, start, trickle, keepalive, key_required, stop, detach, destroy —
and records the refusals alongside it: an unauthenticated upgrade (nginx's own
**401**, no kvmd envelope anywhere), a handshake without the `janus-protocol`
subprotocol (**502**), an unknown plugin, a request name the plugin does not
implement, a body with no `request` and one whose `request` is not a string, a
message to a detached handle, and a keepalive after the session is destroyed.
It also holds what Janus sends unprompted, which is the half no
request/response capture can reach — and what it does *not* send: `media` never
arrives, because Janus events that for the media it receives and a session that
only watches sends none.

The `without_a_viewer` step is the one worth the recording on its own. The
negotiation succeeds in every visible way and no picture comes, because kvmd
runs ustreamer only while some session has asked to be counted as a viewer and
the plugin reads its frames out of ustreamer. The recorder then opens
`/api/ws?stream=1` beside the signalling socket and the same peer connection
starts delivering frames, which is what the `session_events` and `frames` steps
hold.

Nothing device-specific survives in it. Every SDP goes through `scrub_sdp`
first: addresses become `0.0.0.0`, the DTLS fingerprint becomes zeros, the ICE
credentials become placeholders and the candidate lines are dropped outright,
leaving the media lines, the codecs and their parameters — the part a parser
cares about. The ICE server `features` announces is recorded as `<redacted>`,
since it can name a host that is not the device, and frame payloads are not
stored at all, only their dimensions and pixel format. A final guard refuses to
write the file at all if the host, the user name, the password or an address
that is not `0.0.0.0` survived anywhere in it. Re-recording it opens a real
video session for about half a minute and holds a viewer socket open while it
does; nothing needs undoing afterwards, but it is somebody's device, so ask
first.

`redfish_actions.json` is hand-recorded for the same reason: the capture tool
only records GETs that succeed, and everything interesting about the Redfish
actions is either an empty 204 or a refusal. It holds the one system id this
device accepts and four it rejects, the bodiless answers of the `PATCH` stub
and `ComputerSystem.Reset`, the `ResetType` values kvmd refuses, and the
`SetDefaultBootOrder` action every system document advertises without
implementing. No *accepted* `SwitchPort<N>` is in there — the device has no
switch ports to accept.

The one accepted reset was provably a no-op: the capture device runs
`atx.type = disabled`, whose plugin reports a hardcoded `enabled: false` and
whose every action is a stub that raises, and kvmd dispatches a reset of
`Systems/0` only when the ATX is enabled. The target machine's video stayed
online across it. On a device with a working ATX that same step —
`{"ResetType": "On"}` — powers an off host up, and a switch port is not
covered by the `enabled` check at all, so do not re-record any of this without
asking.

Both recorders write into `data/`, resolved through `DATA_DIR` exactly as the
loader resolves it, and print the path they wrote — anywhere else is a file
the loader will not look at. Neither touches
[`data/_manifest.json`](data/_manifest.json); only the capture tool writes it,
and only its `captures` and `device` sections. So a brand-new scenario needs
its `scenarios` entry added by hand before anything can load it, and a
scenario that grows a step loads fine but leaves that entry's `description`
saying less than the file holds.

## What these fixtures do and do not prove

They pin one device in one configuration: ATX disabled, a switch with no ports
attached, one GPIO output channel and no input ones, and the MSD disabled in
the OTG profile — the `msd_*` scenarios above are the exception, recorded with
it temporarily on, and they cover only a single-partition storage.

So a response parsing correctly here does not prove it parses on a populated
switch, on a multi-partition MSD, or on a HID backend other than OTG. Where a
field is nullable or a collection is empty, the fixture usually shows the empty
case only, and a few models rest on kvmd's source rather than on a captured
response — `GPIOInputScheme` and `GPIOInput` have no input channel to be
captured from, the `GPIOView` table items have no populated table, and every
per-port element of `SwitchState` (`SwitchPort`, `SwitchUnit`, the link and
beacon lists) has no switch to be captured from. Prefer
asserting on the shape the fixture does contain, and use the live harness
(`tests/live/`) against a differently configured device when the difference
matters.
