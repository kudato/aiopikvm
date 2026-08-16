# Contract fixtures

Real responses captured from a PiKVM device, used by the test suite instead of
hand-written payloads. Hand-written mocks are how this project shipped five
subsystems whose `get_state()` could never parse a real response: the mock
encoded a shape kvmd does not produce, and the tests passed anyway.

| | |
|---|---|
| Device | PiKVM v3 (Raspberry Pi Compute Module 4) |
| kvmd | 4.186 |
| Streamer | ustreamer 6.61 |

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
- anything else listed in `PIKVM_SCRUB`, for device-specific values the rules
  above cannot know about — port names, a monitor serial inside a captured
  EDID, an internal DNS suffix.

A final guard refuses to write a file that still contains a redacted string or
an address that is not the placeholder, so a leak fails the capture instead of
silently landing in git.

Two endpoints are deliberately **not** captured: `/api/streamer/snapshot` and
its OCR variant return whatever is on the attached host's screen.

`auth_roundtrip.json` was recorded by hand (with `httpx`, following the steps
listed in the file) rather than by the capture tool, which stays read-only —
the sequence logs in and out and would invalidate a session.

The four `msd_*` scenarios were recorded the same way, and needed the device
reconfigured for the duration: `otg.devices.msd` is `enabled: false` on the
capture device, so `/api/msd` reports `drive: null, storage: null` and shows
nothing of the online shape. With `enabled` and `start` turned on and kvmd
restarted, they cover a drive holding an image (`msd_image`), an empty drive
with an image in storage (`msd_online`), and both transfer counters mid-flight
(`msd_uploading`, `msd_downloading`, sampled while a temporary image was being
written and read back). The temporary images were removed and the OTG profile
restored afterwards. Reproducing them means changing somebody's device, so ask
first — and note that toggling the MSD recreates the USB gadget, which briefly
disconnects the keyboard, mouse and audio the target host sees.

## What these fixtures do and do not prove

They pin one device in one configuration: ATX disabled, a single GPIO channel,
a switch with no ports attached, and the MSD disabled in the OTG profile — the
`msd_*` scenarios above are the exception, recorded with it temporarily on, and
they cover only a single-partition storage. A response parsing correctly here
does not prove it parses on a populated switch, on a multi-partition MSD, or on
a HID backend other than OTG — where a field is nullable or a collection is
empty, the fixture usually shows the empty case only. Prefer asserting on the
shape the fixture does contain, and use the live harness (`tests/live/`)
against a differently configured device when the difference matters.
