"""Refresh the contract fixtures from a live PiKVM device.

Usage::

    PIKVM_URL=https://pikvm.local PIKVM_PASSWD=secret \\
        uv run python -m tests.fixtures.capture

Environment variables:
    ``PIKVM_URL`` — device base URL (required).
    ``PIKVM_USER`` — user name (default ``admin``).
    ``PIKVM_PASSWD`` — password (required).
    ``PIKVM_TOTP`` — TOTP code, appended to the password (optional).
    ``PIKVM_SCRUB`` — extra comma-separated strings to redact, for anything
    device-specific the built-in rules do not know about (switch port names,
    an internal DNS suffix, ...). Note that it is a literal text replacement:
    a value that only appears hex-encoded, as inside an EDID blob, will not
    be found by it — EDIDs are rewritten by :func:`scrub_edid` instead.

No endpoint is mutated: every capture is a GET. The event socket does ask
for video, which starts the streamer when nothing else was watching — the
corpus needs it running, and kvmd stops it again when the socket closes.

Responses are sanitized before they are written (device serial, host names,
IP and MAC addresses) and a guard refuses to write a file that still
contains the target host, the credentials or an address-shaped string. A
second guard refuses a corpus that lost something the contract tests read,
and nothing is written until both have passed.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import sys
from typing import Any, NamedTuple
from urllib.parse import urlparse

from aiopikvm import PiKVM, PiKVMError, PiKVMWebSocket
from tests.fixtures import DATA_DIR, MANIFEST_PATH

HOST_PLACEHOLDER = "pikvm"
SERIAL_PLACEHOLDER = "0" * 16
MONITOR_PLACEHOLDER = "DUMMY SCREEN"
MONITOR_SERIAL_PLACEHOLDER = "0000000"
IP_PLACEHOLDER = "192.0.2.10"  # RFC 5737 documentation range
MAC_PLACEHOLDER = "00:00:00:00:00:00"
REDACTED = "<redacted>"

STREAMER_WARMUP = 3.0
"""How long to let the streamer come online before the REST captures run."""

WS_TAIL_SECONDS = 5.0
"""How long to keep listening after the REST captures and the ping.

Long enough for the periodic per-subsystem updates that follow the initial
burst — those are what carry a partial event, and without one the merge the
contract tests exercise would be proven by nothing.
"""

_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_MAC = re.compile(r"\b(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}\b")
_ACCESS_CLIENT = re.compile(r"(\[[^\]]*?/\s*)[^\]\s]+(\])")
"""Client address field of a kvmd ``aiohttp.access`` log line."""

_EDID_HEX = re.compile(r"[0-9A-Fa-f]{256}(?:[0-9A-Fa-f]{256})?")
"""An EDID blob as the switch reports it: one or two 128-byte blocks."""

_PATH_RULES: tuple[tuple[tuple[str, ...], object], ...] = (
    (("platform", "serial"), SERIAL_PLACEHOLDER),
    (("server", "host"), HOST_PLACEHOLDER),
    (("node", "host"), HOST_PLACEHOLDER),
    (("HostName",), HOST_PLACEHOLDER),
    (("parsed", "monitor_name"), MONITOR_PLACEHOLDER),
    (("parsed", "monitor_serial"), MONITOR_SERIAL_PLACEHOLDER),
    (("parsed", "mfc_id"), "AAA"),
    (("parsed", "product_id"), 0),
    (("parsed", "serial"), 0),
)
"""Key paths (matched on the trailing keys) replaced by a placeholder.

The ``parsed`` rules cover the switch EDID catalogue, which identifies
whatever monitor the ports were learned from. They only reach the decoded
block — the same values sit in the raw ``data`` hex, which :func:`scrub_edid`
rewrites separately.
"""


class Endpoint(NamedTuple):
    """A read-only endpoint to capture."""

    name: str
    path: str
    params: dict[str, Any] | None = None
    kind: str = "json"


ENDPOINTS: tuple[Endpoint, ...] = (
    Endpoint("info", "/api/info"),
    Endpoint("info_hw_system", "/api/info", {"fields": "hw,system"}),
    Endpoint("info_hw", "/api/info", {"fields": "hw"}),
    Endpoint("info_legacy0", "/api/info", {"legacy": 0}),
    Endpoint("log_seek60", "/api/log", {"seek": 60}, "text"),
    Endpoint("atx", "/api/atx"),
    Endpoint("hid", "/api/hid"),
    Endpoint("hid_keymaps", "/api/hid/keymaps"),
    Endpoint("hid_inactivity", "/api/hid/inactivity"),
    Endpoint("msd", "/api/msd"),
    Endpoint("gpio", "/api/gpio"),
    Endpoint("streamer", "/api/streamer"),
    Endpoint("streamer_ocr", "/api/streamer/ocr"),
    Endpoint("switch", "/api/switch"),
    Endpoint("prometheus_metrics", "/api/export/prometheus/metrics", None, "text"),
    Endpoint("redfish_root", "/api/redfish/v1"),
    Endpoint("redfish_systems", "/api/redfish/v1/Systems"),
    Endpoint("redfish_system_0", "/api/redfish/v1/Systems/0"),
    Endpoint("redfish_managers", "/api/redfish/v1/Managers"),
    Endpoint("redfish_manager_bmc", "/api/redfish/v1/Managers/BMC"),
    Endpoint("redfish_vm", "/api/redfish/v1/Managers/BMC/VirtualMedia"),
    Endpoint("redfish_vm_msd", "/api/redfish/v1/Managers/BMC/VirtualMedia/MSD"),
)

# Pairs of ``(secret, placeholder)`` applied to every captured payload.
type Redactions = tuple[tuple[str, str], ...]


def redactions(url: str, user: str, passwd: str, extra: str = "") -> Redactions:
    """Build the redaction list for a device.

    Args:
        url: Device base URL.
        user: User name used for the capture.
        passwd: Password used for the capture.
        extra: Comma-separated extra strings to redact.

    Returns:
        ``(secret, placeholder)`` pairs, longest secret first so that a
        FQDN is replaced before its own labels.
    """
    pairs: list[tuple[str, str]] = []
    host = urlparse(url).hostname or ""
    if host:
        pairs.append((host, HOST_PLACEHOLDER))
        # A FQDN also leaks through its labels (log lines print the short
        # name); skip short ones like "com" to avoid mangling normal words.
        pairs += [
            (label, HOST_PLACEHOLDER) for label in host.split(".") if len(label) >= 4
        ]
    if user:
        pairs.append((user, "admin"))
    if passwd:
        pairs.append((passwd, REDACTED))
    pairs += [(item.strip(), REDACTED) for item in extra.split(",") if item.strip()]
    return tuple(sorted(set(pairs), key=lambda pair: (-len(pair[0]), pair[0])))


def _edid_descriptor(tag: int, text: str) -> bytes:
    """Build one 18-byte EDID display descriptor holding *text*."""
    body = (text.encode("ascii") + b"\x0a").ljust(13, b"\x20")
    return bytes([0, 0, 0, tag, 0]) + body


def scrub_edid(blob: str) -> str:
    """Rewrite an EDID blob so that it identifies no particular monitor.

    The switch stores the EDID of whatever was plugged into a port, and the
    manufacturer, product id, serial and monitor name all sit inside the raw
    bytes as well as in the block kvmd decodes. Replacing them by hand would
    leave the block-0 checksum wrong, so it is recomputed here.

    Args:
        blob: EDID as an uppercase hex string, 128 or 256 bytes.

    Returns:
        The rewritten blob, same length, checksums valid.
    """
    data = bytearray.fromhex(blob)
    mfc = 0
    for letter in "AAA":
        mfc = (mfc << 5) | (ord(letter) - ord("A") + 1)
    data[8:10] = mfc.to_bytes(2, "big")
    data[10:12] = (0).to_bytes(2, "little")
    data[12:16] = (0).to_bytes(4, "little")
    for start in range(54, 126, 18):
        if bytes(data[start : start + 3]) != b"\x00\x00\x00":
            continue
        if data[start + 3] == 0xFF:
            data[start : start + 18] = _edid_descriptor(
                0xFF, MONITOR_SERIAL_PLACEHOLDER
            )
        elif data[start + 3] == 0xFC:
            data[start : start + 18] = _edid_descriptor(0xFC, MONITOR_PLACEHOLDER)
    data[127] = (-sum(data[0:127])) % 256
    if len(data) > 128:
        data[255] = (-sum(data[128:255])) % 256
    return data.hex().upper()


def scrub_json(value: Any, path: tuple[str, ...] = ()) -> Any:
    """Replace device-identifying values by key path.

    Args:
        value: Parsed JSON value.
        path: Key path of *value* within the document.

    Returns:
        A copy of *value* with the values listed in ``_PATH_RULES``
        replaced by their placeholder, and any EDID blob rewritten by
        :func:`scrub_edid`.
    """
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            sub = (*path, key)
            replacement = next(
                (new for rule, new in _PATH_RULES if sub[-len(rule) :] == rule), None
            )
            if replacement is not None:
                out[key] = replacement
            elif key == "data" and isinstance(item, str) and _EDID_HEX.fullmatch(item):
                out[key] = scrub_edid(item)
            else:
                out[key] = scrub_json(item, sub)
        return out
    if isinstance(value, list):
        return [scrub_json(item, path) for item in value]
    return value


def scrub_text(text: str, secrets: Redactions, *, log_lines: bool = False) -> str:
    """Redact secrets and addresses from captured text.

    Args:
        text: Captured text (a log, a metrics dump or serialized JSON).
        secrets: Redaction pairs from :func:`redactions`.
        log_lines: Also redact the client address of kvmd
            ``aiohttp.access`` lines, which catches clients the IPv4 rule
            does not match. Only for plain-text captures — the pattern
            keys on square brackets and would be ambiguous inside JSON.

    Returns:
        The sanitized text.
    """
    for secret, placeholder in secrets:
        text = re.sub(re.escape(secret), placeholder, text, flags=re.IGNORECASE)
    if log_lines:
        text = _ACCESS_CLIENT.sub(rf"\g<1>{IP_PLACEHOLDER}\g<2>", text)
    text = _IPV4.sub(IP_PLACEHOLDER, text)
    return _MAC.sub(MAC_PLACEHOLDER, text)


def assert_clean(name: str, text: str, secrets: Redactions) -> None:
    """Fail if sanitized text still carries device-identifying data.

    Args:
        name: Capture name, for the error message.
        text: Sanitized text about to be written.
        secrets: Redaction pairs from :func:`redactions`.

    Raises:
        ValueError: If a secret, a non-placeholder IP or a non-placeholder
            MAC address survived sanitization.
    """
    for secret, placeholder in secrets:
        if secret.lower() == placeholder.lower():
            continue
        if secret.lower() in text.lower():
            raise ValueError(f"{name}: secret survived sanitization")
    leaked = {found for found in _IPV4.findall(text) if found != IP_PLACEHOLDER}
    leaked |= {found for found in _MAC.findall(text) if found != MAC_PLACEHOLDER}
    if leaked:
        raise ValueError(
            f"{name}: {len(leaked)} address(es) survived sanitization; "
            f"add them to PIKVM_SCRUB"
        )


def render(payload: Any, kind: str, secrets: Redactions) -> str:
    """Sanitize a captured payload and render it for writing.

    Args:
        payload: Parsed JSON value or raw text.
        kind: ``"json"`` or ``"text"``.
        secrets: Redaction pairs from :func:`redactions`.

    Returns:
        File contents, newline-terminated.

    Raises:
        ValueError: If sanitizing broke the JSON document.
    """
    if kind != "json":
        return scrub_text(str(payload), secrets, log_lines=True).rstrip("\n") + "\n"
    text = scrub_text(
        json.dumps(scrub_json(payload), indent=2, ensure_ascii=False), secrets
    )
    try:
        json.loads(text)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise ValueError(f"sanitization produced invalid JSON: {exc}") from exc
    return text + "\n"


async def _collect(ws: PiKVMWebSocket, lines: list[str]) -> None:
    """Append every event the socket yields, as a JSON Lines entry.

    Runs until cancelled, so that the REST captures below happen while the
    socket is open and the streamer it asked for is running.

    Args:
        ws: Open event socket.
        lines: List to append the rendered entries to, in arrival order.
    """
    async for event in ws.events():
        entry = {"index": len(lines), "msg": scrub_json(event)}
        lines.append(json.dumps(entry, ensure_ascii=False))


def assert_complete(events: list[dict[str, Any]], streamer: Any) -> None:
    """Fail if the capture lost something the contract tests read.

    A capture that quietly under-samples is worse than none: the suite goes
    green against a corpus that no longer proves what it used to. Each check
    here stands for a test that would otherwise start passing vacuously.

    Args:
        events: The captured events, in arrival order.
        streamer: The parsed ``/api/streamer`` capture.

    Raises:
        ValueError: If no pong came back, if no event carries a subset of
            the keys its first one did, or if the streamer was not running.
    """
    keys: dict[str, list[set[str]]] = {}
    for event in events:
        keys.setdefault(str(event["event_type"]), []).append(set(event["event"]))
    if "pong" not in keys:
        raise ValueError("ws_events: no pong came back; the ping was not answered")
    if not any(later < sent[0] for sent in keys.values() for later in sent[1:]):
        raise ValueError(
            "ws_events: no event carries a subset of the keys the first one "
            "did, so nothing proves the state merge; listen for longer"
        )
    if streamer.get("result", {}).get("streamer") is None:
        raise ValueError(
            "streamer: the streamer was not running, so its state block is "
            "null and the running case goes uncaptured"
        )


async def main() -> int:
    """Capture every endpoint and rewrite the fixture data directory.

    Returns:
        Process exit code.
    """
    url = os.environ.get("PIKVM_URL", "")
    passwd = os.environ.get("PIKVM_PASSWD", "")
    if not url or not passwd:
        print("PIKVM_URL and PIKVM_PASSWD are required", file=sys.stderr)
        return 2
    user = os.environ.get("PIKVM_USER", "admin")
    secrets = redactions(url, user, passwd, os.environ.get("PIKVM_SCRUB", ""))

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    captures: dict[str, Any] = {}

    pending: dict[str, str] = {}
    lines: list[str] = []

    async with PiKVM(
        url, user=user, passwd=passwd, totp=os.environ.get("PIKVM_TOTP")
    ) as kvm:
        # The socket asks for video on purpose. kvmd runs the streamer while
        # a session wants it, and both the running block in /api/streamer and
        # the partial updates that follow only exist while it does. It stops
        # again when this socket closes.
        async with kvm.ws(stream=True) as ws:
            reader = asyncio.create_task(_collect(ws, lines))
            await asyncio.sleep(STREAMER_WARMUP)

            for endpoint in ENDPOINTS:
                response = await kvm.request(
                    "GET", endpoint.path, params=endpoint.params
                )
                payload = response.json() if endpoint.kind == "json" else response.text
                text = render(payload, endpoint.kind, secrets)
                assert_clean(endpoint.name, text, secrets)
                file = f"{endpoint.name}.{'json' if endpoint.kind == 'json' else 'txt'}"
                pending[file] = text
                captures[endpoint.name] = {
                    "method": "GET",
                    "path": endpoint.path,
                    "params": endpoint.params,
                    "status": response.status_code,
                    "content_type": response.headers.get("content-type", ""),
                    "file": file,
                }
                print(f"{endpoint.name}: {response.status_code} -> {file}")

            await ws.ping()
            await asyncio.sleep(WS_TAIL_SECONDS)
            reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await reader

    ws_file = str(manifest["scenarios"]["ws_events"]["file"])
    ws_text = scrub_text("\n".join(lines), secrets) + "\n"
    assert_clean("ws_events", ws_text, secrets)
    pending[ws_file] = ws_text

    info = json.loads(pending[str(captures["info"]["file"])])
    assert_complete(
        [json.loads(line)["msg"] for line in lines],
        json.loads(pending[str(captures["streamer"]["file"])]),
    )

    for file, text in pending.items():
        (DATA_DIR / file).write_text(text, encoding="utf-8")
    print(f"ws_events -> {ws_file}")

    system = info["result"]["system"]
    manifest["device"] = {
        "kvmd": system["kvmd"]["version"],
        "streamer": f"{system['streamer']['app']} {system['streamer']['version']}",
        "platform": info["result"]["hw"]["platform"],
    }
    manifest["captures"] = captures
    MANIFEST_PATH.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"manifest -> {MANIFEST_PATH.name}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except PiKVMError as exc:
        print(f"capture failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
