"""MSDResource tests."""

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import respx

from aiopikvm import (
    APIError,
    AuthError,
    ConfigurationError,
    ConnectError,
    PiKVM,
    ResponseError,
)
from tests.fixtures import load_json

WRITE = load_json("msd_write")
STEPS = {entry["name"]: entry for entry in WRITE["steps"]}


def step(name: str) -> dict[str, Any]:
    """Return one recorded step of the ``msd_write`` scenario."""
    return STEPS[name]


def body_bytes(name: str) -> bytes:
    """Return a recorded step's body exactly as it came off the wire."""
    entry = step(name)
    if "body_text" in entry:
        return str(entry["body_text"]).encode()
    return json.dumps(entry["body"]).encode()


def replay(name: str) -> httpx.Response:
    """Rebuild the response a recorded step produced, body and all."""
    entry = step(name)
    return httpx.Response(
        entry["status"],
        headers={"content-type": entry["content_type"]},
        content=body_bytes(name),
    )


class _LineStream(httpx.AsyncByteStream):
    """Deliver an NDJSON body one line at a time, then optionally break.

    kvmd sends each record as its own chunk, and when the download fails it
    sends the failure as one more record and then lets the exception escape
    the handler — so the chunked body never gets its terminating chunk and
    httpx raises on the read that follows. Both halves matter: a client that
    waits for the whole body sees only the crash, and never the record that
    says what went wrong.
    """

    def __init__(self, payload: bytes, *, broken: bool = False) -> None:
        self._payload = payload
        self._broken = broken

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for line in self._payload.splitlines(keepends=True):
            yield line
        if self._broken:
            raise httpx.RemoteProtocolError(
                "peer closed connection without sending complete message body"
            )


def replay_stream(name: str) -> httpx.Response:
    """Rebuild a recorded NDJSON step as a streamed response."""
    entry = step(name)
    return httpx.Response(
        entry["status"],
        headers={"content-type": entry["content_type"]},
        stream=_LineStream(body_bytes(name), broken=bool(entry.get("stream_broken"))),
    )


def records(name: str) -> list[dict[str, Any]]:
    """Return the parsed envelopes of a recorded NDJSON step."""
    return [
        json.loads(line) for line in str(step(name)["body_text"]).splitlines() if line
    ]


async def test_get_state_offline(mock_api: respx.MockRouter, client: PiKVM) -> None:
    # The MSD is disabled in the OTG profile: kvmd nulls both blocks, which is
    # the shape the old model could not parse at all.
    mock_api.get("/api/msd").mock(
        return_value=httpx.Response(200, json=load_json("msd"))
    )
    state = await client.msd.get_state()
    assert state.enabled is True
    assert state.online is False
    assert state.drive is None
    assert state.storage is None


async def test_get_state_with_an_image_in_the_drive(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    mock_api.get("/api/msd").mock(
        return_value=httpx.Response(200, json=load_json("msd_image"))
    )
    state = await client.msd.get_state()
    assert state.online is True
    assert state.drive is not None
    assert state.drive.cdrom is True
    assert state.drive.connected is False
    assert state.drive.image is not None
    assert state.drive.image.name == "test-1m.iso"
    assert state.drive.image.in_storage is True
    assert state.drive.image.size == 1048576
    assert state.storage is not None
    assert state.storage.images["test-1m.iso"].complete is True
    # kvmd reports free space per partition; the root one is keyed by "".
    assert state.storage.parts[""].writable is True
    assert state.storage.parts[""].free > 0


async def test_get_state_with_an_empty_drive(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    mock_api.get("/api/msd").mock(
        return_value=httpx.Response(200, json=load_json("msd_online"))
    )
    state = await client.msd.get_state()
    assert state.drive is not None
    assert state.drive.image is None
    assert state.storage is not None
    assert state.storage.images


async def test_get_state_while_uploading(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    mock_api.get("/api/msd").mock(
        return_value=httpx.Response(200, json=load_json("msd_uploading"))
    )
    state = await client.msd.get_state()
    assert state.storage is not None
    assert state.storage.uploading is not None
    assert state.storage.uploading.name == "test-slow.iso"
    assert state.storage.uploading.written < state.storage.uploading.size
    assert state.storage.images["test-slow.iso"].complete is False


async def test_get_state_while_downloading(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    mock_api.get("/api/msd").mock(
        return_value=httpx.Response(200, json=load_json("msd_downloading"))
    )
    state = await client.msd.get_state()
    assert state.storage is not None
    assert state.storage.downloading is not None
    assert state.storage.downloading.name == "test-8m.iso"
    assert state.storage.downloading.readed < state.storage.downloading.size


# --- upload ----------------------------------------------------------------


async def test_upload_returns_the_write_info(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    # The body is the recorded one, so the numbers describe the 1 MiB image
    # that was actually written rather than the bytes this test sends.
    mock_api.post("/api/msd/write").mock(return_value=replay("write_ok"))
    info = await client.msd.upload("test-write.iso", b"fake-iso-data")
    assert info.name == "test-write.iso"
    assert info.written == info.size
    request = mock_api.calls[-1].request
    assert request.url.params["image"] == "test-write.iso"
    assert request.headers["content-length"] == "13"


async def test_upload_reports_the_name_kvmd_stored(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    # A prefix is joined onto the name server-side, so the only way to learn
    # what to pass to set_params() or remove() is to read it back.
    mock_api.post("/api/msd/write").mock(return_value=replay("write_prefix"))
    info = await client.msd.upload("test-write.iso", b"data", prefix="isos")
    assert info.name == "isos/test-write.iso"
    params = mock_api.calls[-1].request.url.params
    assert params["prefix"] == "isos"
    assert params["image"] == "test-write.iso"


async def test_upload_into_a_missing_prefix_directory(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    # kvmd writes the .incomplete marker before it creates the directory, so
    # this is an unhandled FileNotFoundError: a plain-text 500 with no error
    # block for the message to come from.
    mock_api.post("/api/msd/write").mock(
        return_value=replay("write_prefix_missing_dir")
    )
    with pytest.raises(APIError) as caught:
        await client.msd.upload("test-write.iso", b"data", prefix="nested")
    assert caught.value.status_code == 500
    assert caught.value.error == ""


async def test_upload_remove_incomplete(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    mock_api.post("/api/msd/write").mock(return_value=replay("write_ok"))
    await client.msd.upload("test.iso", b"data", remove_incomplete=True)
    assert mock_api.calls[-1].request.url.params["remove_incomplete"] == "1"


async def test_upload_rejects_a_body_without_the_write_info(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    # The empty envelope this used to be mocked with is not what kvmd sends,
    # and a caller reading the stored name off it would get an attribute error
    # far from the cause.
    mock_api.post("/api/msd/write").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    with pytest.raises(ResponseError, match="no image block"):
        await client.msd.upload("test.iso", b"data")


async def test_upload_streaming(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/msd/write").mock(return_value=replay("write_ok"))

    async def data_gen() -> AsyncIterator[bytes]:
        yield b"chunk1"
        yield b"chunk2"

    await client.msd.upload("test.iso", data_gen(), size=12)
    request = mock_api.calls[-1].request
    assert "image=test.iso" in str(request.url)
    # kvmd takes the image size from Content-Length; httpx would otherwise
    # frame an iterator as Transfer-Encoding: chunked and kvmd answers 400.
    assert request.headers["content-length"] == "12"
    assert "transfer-encoding" not in request.headers


async def test_upload_without_content_length_is_what_kvmd_refuses(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    # The recorded proof of why upload() declares the length: a chunked body
    # leaves kvmd's size validator with None, and no image is written.
    entry = step("write_chunked")
    assert entry["status"] == 400
    assert entry["body"]["result"]["error_msg"] == "None argument is not a valid int"
    mock_api.post("/api/msd/write").mock(return_value=replay("write_chunked"))
    with pytest.raises(APIError, match="not a valid int"):
        await client.msd.upload("test.iso", b"data")


async def test_upload_without_a_name_is_refused(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    mock_api.post("/api/msd/write").mock(return_value=replay("write_no_image"))
    with pytest.raises(APIError, match="not a valid MSD image name"):
        await client.msd.upload("", b"data")


async def test_upload_streaming_without_size(client: PiKVM) -> None:
    async def data_gen() -> AsyncIterator[bytes]:
        yield b"chunk1"  # pragma: no cover - never consumed

    with pytest.raises(ConfigurationError, match="size of a streamed image"):
        await client.msd.upload("test.iso", data_gen())


async def test_upload_negative_size(client: PiKVM) -> None:
    async def data_gen() -> AsyncIterator[bytes]:
        yield b"chunk1"  # pragma: no cover - never consumed

    with pytest.raises(ConfigurationError, match="negative size"):
        await client.msd.upload("test.iso", data_gen(), size=-1)


async def test_upload_streaming_size_too_small(client: PiKVM) -> None:
    # An undercount is the dangerous one: kvmd reads exactly as many bytes as
    # Content-Length announced and stores the truncated image as complete,
    # while h11 raises LocalProtocolError outside the aiopikvm hierarchy. The
    # body is counted before it is handed over, so nothing is sent at all.
    async def data_gen() -> AsyncIterator[bytes]:
        yield b"chunk1"
        yield b"chunk2"

    with pytest.raises(ConfigurationError, match="more data than that"):
        await client.msd.upload("test.iso", data_gen(), size=6)


async def test_upload_streaming_size_too_large(client: PiKVM) -> None:
    async def data_gen() -> AsyncIterator[bytes]:
        yield b"chunk1"

    with pytest.raises(ConfigurationError, match="ended after 6 bytes"):
        await client.msd.upload("test.iso", data_gen(), size=30)


async def test_upload_bytes_ignores_size(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    mock_api.post("/api/msd/write").mock(return_value=replay("write_ok"))
    await client.msd.upload("test.iso", b"fake-iso-data", size=999)
    assert mock_api.calls[-1].request.headers["content-length"] == "13"


async def test_upload_streaming_auth_error(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    mock_api.post("/api/msd/write").mock(return_value=httpx.Response(401))

    async def data_gen() -> AsyncIterator[bytes]:
        yield b"chunk1"

    with pytest.raises(AuthError):
        await client.msd.upload("test.iso", data_gen(), size=6)


# --- upload_remote ---------------------------------------------------------


async def test_upload_remote_returns_the_last_record(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    mock_api.post("/api/msd/write_remote").mock(return_value=replay_stream("remote_ok"))
    info = await client.msd.upload_remote("http://localhost:8099/slow-4m.iso")
    assert info.name == "test-remote.iso"
    assert info.written == info.size


async def test_upload_remote_yields_progress(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    # kvmd sends one record before the first byte, one about every second
    # while the download runs, and one when it ends. The old client called
    # .json() on the concatenation of all of them.
    mock_api.post("/api/msd/write_remote").mock(return_value=replay_stream("remote_ok"))
    seen = [
        record
        async for record in client.msd.upload_remote_progress(
            "http://localhost:8099/slow-4m.iso"
        )
    ]
    assert len(seen) == len(records("remote_ok"))
    assert len(seen) > 1
    assert seen[0].written == 0
    assert [record.written for record in seen] == sorted(
        record.written for record in seen
    )
    assert seen[-1].written == seen[-1].size


async def test_upload_remote_raises_the_failure_record(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    # The download failed under HTTP 200, and the connection broke right after
    # the record that says so. Reading the record first is what turns this
    # into the actual reason rather than "the peer closed the connection".
    assert step("remote_broken")["status"] == 200
    assert step("remote_broken")["stream_broken"] == "RemoteProtocolError"
    mock_api.post("/api/msd/write_remote").mock(
        return_value=replay_stream("remote_broken")
    )
    with pytest.raises(APIError) as caught:
        await client.msd.upload_remote("http://localhost:8099/truncated.iso")
    assert caught.value.error == "ClientPayloadError"
    assert not isinstance(caught.value, ConnectError)


async def test_upload_remote_progress_survives_until_the_failure(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    mock_api.post("/api/msd/write_remote").mock(
        return_value=replay_stream("remote_broken")
    )
    seen = []
    with pytest.raises(APIError):
        async for record in client.msd.upload_remote_progress(
            "http://localhost:8099/truncated.iso"
        ):
            seen.append(record)
    ok_records = [entry for entry in records("remote_broken") if entry["ok"]]
    assert len(seen) == len(ok_records)
    assert seen[-1].written < seen[-1].size


@pytest.mark.parametrize(
    "name",
    [
        "remote_exists",
        "remote_no_length",
        "remote_missing",
        "remote_unreachable",
        "remote_bad_scheme",
        "remote_no_url",
    ],
)
async def test_upload_remote_refusals(
    mock_api: respx.MockRouter, client: PiKVM, name: str
) -> None:
    """Everything kvmd refuses before it streams arrives as an error status."""
    entry = step(name)
    assert entry["status"] == 400
    mock_api.post("/api/msd/write_remote").mock(return_value=replay(name))
    with pytest.raises(APIError) as caught:
        await client.msd.upload_remote("http://localhost:8099/slow-4m.iso")
    assert caught.value.status_code == 400
    assert caught.value.error == entry["body"]["result"]["error"]


async def test_upload_remote_sends_every_parameter(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    mock_api.post("/api/msd/write_remote").mock(return_value=replay_stream("remote_ok"))
    await client.msd.upload_remote(
        "http://localhost:8099/slow-4m.iso",
        name="stored.iso",
        prefix="isos",
        insecure=True,
        remove_incomplete=True,
        connect_timeout=2.5,
    )
    params = mock_api.calls[-1].request.url.params
    assert params["url"] == "http://localhost:8099/slow-4m.iso"
    assert params["image"] == "stored.iso"
    assert params["prefix"] == "isos"
    assert params["insecure"] == "1"
    assert params["remove_incomplete"] == "1"
    # kvmd calls it "timeout", and it bounds connecting to the origin rather
    # than the download or this client's own wait.
    assert params["timeout"] == "2.5"


async def test_upload_remote_sends_only_the_url_by_default(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    mock_api.post("/api/msd/write_remote").mock(return_value=replay_stream("remote_ok"))
    await client.msd.upload_remote("http://localhost:8099/slow-4m.iso")
    assert list(mock_api.calls[-1].request.url.params) == ["url"]


async def test_upload_remote_disables_the_read_timeout(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    # The response stays open for the length of the download, and kvmd allows
    # that a week; the 10 s client default would kill it mid-transfer.
    mock_api.post("/api/msd/write_remote").mock(return_value=replay_stream("remote_ok"))
    await client.msd.upload_remote("http://localhost:8099/slow-4m.iso")
    timeout = mock_api.calls[-1].request.extensions["timeout"]
    assert timeout["read"] is None
    assert timeout["connect"] == 10.0


async def test_upload_remote_explicit_timeout(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    mock_api.post("/api/msd/write_remote").mock(return_value=replay_stream("remote_ok"))
    await client.msd.upload_remote(
        "http://localhost:8099/slow-4m.iso", timeout=httpx.Timeout(5.0, read=90.0)
    )
    assert mock_api.calls[-1].request.extensions["timeout"]["read"] == 90.0


async def test_upload_remote_without_any_record(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    mock_api.post("/api/msd/write_remote").mock(
        return_value=httpx.Response(
            200, headers={"content-type": "application/x-ndjson"}, content=b""
        )
    )
    with pytest.raises(ResponseError, match="without a single progress record"):
        await client.msd.upload_remote("http://localhost:8099/slow-4m.iso")


async def test_upload_remote_line_that_is_not_json(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    mock_api.post("/api/msd/write_remote").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "application/x-ndjson"},
            content=b"<html>nginx</html>\r\n",
        )
    )
    with pytest.raises(ResponseError, match="not JSON"):
        await client.msd.upload_remote("http://localhost:8099/slow-4m.iso")


async def test_upload_remote_record_without_the_write_info(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    mock_api.post("/api/msd/write_remote").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "application/x-ndjson"},
            content=b'{"ok": true, "result": {}}\r\n',
        )
    )
    with pytest.raises(ResponseError, match="no image block"):
        await client.msd.upload_remote("http://localhost:8099/slow-4m.iso")


async def test_upload_remote_record_that_is_not_an_object(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    mock_api.post("/api/msd/write_remote").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "application/x-ndjson"},
            content=b"[1, 2, 3]\r\n",
        )
    )
    with pytest.raises(ResponseError, match="expected an object"):
        await client.msd.upload_remote("http://localhost:8099/slow-4m.iso")


async def test_upload_remote_broken_before_any_record(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    # Nothing to report the reason with, so the transport failure is the
    # answer — inside the hierarchy rather than as a bare httpx error.
    mock_api.post("/api/msd/write_remote").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "application/x-ndjson"},
            stream=_LineStream(b"", broken=True),
        )
    )
    with pytest.raises(ConnectError):
        await client.msd.upload_remote("http://localhost:8099/slow-4m.iso")


# --- everything else -------------------------------------------------------


async def test_set_connected(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/msd/set_connected").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    await client.msd.set_connected(True)


async def test_remove(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/msd/remove").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    await client.msd.remove("test.iso")


async def test_remove_takes_the_stored_name(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    # An image written under a prefix is listed, and removed, by the joined
    # name rather than the one that was uploaded.
    mock_api.post("/api/msd/remove").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    await client.msd.remove("isos/test-write.iso")
    assert mock_api.calls[-1].request.url.params["image"] == "isos/test-write.iso"


async def test_reset(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/msd/reset").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    await client.msd.reset()


async def test_set_params(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/msd/set_params").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    await client.msd.set_params(cdrom=True, rw=False)
    request = mock_api.calls[-1].request
    assert "cdrom=1" in str(request.url)
    assert "rw=0" in str(request.url)


async def test_set_params_partial(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/msd/set_params").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    await client.msd.set_params(cdrom=True)
    request = mock_api.calls[-1].request
    assert "cdrom=1" in str(request.url)
    assert "rw" not in str(request.url)


async def test_set_params_image(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/msd/set_params").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    await client.msd.set_params(image="boot.iso")
    assert mock_api.calls[-1].request.url.params["image"] == "boot.iso"


async def test_set_params_ejects_with_empty_image(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    # kvmd reads an empty `image` as "eject", which is why the parameter is
    # sent whenever it is not None rather than whenever it is truthy.
    mock_api.post("/api/msd/set_params").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    await client.msd.set_params(image="")
    assert mock_api.calls[-1].request.url.params["image"] == ""


async def test_download(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.get("/api/msd/read").mock(
        return_value=httpx.Response(200, content=b"iso-bytes")
    )
    chunks = [chunk async for chunk in client.msd.download("boot.iso")]
    assert b"".join(chunks) == b"iso-bytes"
    params = mock_api.calls[-1].request.url.params
    assert params["image"] == "boot.iso"
    assert "compress" not in params


async def test_download_compressed(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.get("/api/msd/read").mock(
        return_value=httpx.Response(200, content=b"compressed")
    )
    chunks = [chunk async for chunk in client.msd.download("boot.iso", compress="zstd")]
    assert b"".join(chunks) == b"compressed"
    assert mock_api.calls[-1].request.url.params["compress"] == "zstd"


async def test_download_disables_the_read_timeout(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    # An image transfer outlives the 10 s client default many times over.
    mock_api.get("/api/msd/read").mock(return_value=httpx.Response(200, content=b""))
    async for _ in client.msd.download("boot.iso"):
        pass  # pragma: no cover - the body is empty
    timeout = mock_api.calls[-1].request.extensions["timeout"]
    assert timeout["read"] is None
    assert timeout["connect"] == 10.0


async def test_download_explicit_timeout(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    mock_api.get("/api/msd/read").mock(return_value=httpx.Response(200, content=b""))
    async for _ in client.msd.download("boot.iso", timeout=42.0):
        pass  # pragma: no cover - the body is empty
    assert mock_api.calls[-1].request.extensions["timeout"]["read"] == 42.0


async def test_download_error_status(mock_api: respx.MockRouter, client: PiKVM) -> None:
    """A refused read raises before the first chunk, with kvmd's own reason.

    The status is 400, not 503: everything `/api/msd/read` refuses for is a
    `MsdOperationError`, which kvmd maps to 400 along with every other
    `OperationError`. It answers 503 nowhere in the MSD API — only the
    streamer raises the error class that carries that status.

    The payload is written out rather than loaded, since no capture in this
    repository shows a *read* being refused. Its shape is a real one all the
    same: the `remote_exists` step of `msd_write` is the same subsystem
    refusing the same way, an `MsdOperationError` under a 400 (#68).
    """
    mock_api.get("/api/msd/read").mock(
        return_value=httpx.Response(
            400,
            json={
                "ok": False,
                "result": {
                    "error": "MsdOfflineError",
                    "error_msg": "MSD is not found",
                },
            },
        )
    )
    with pytest.raises(APIError, match="MSD is not found") as info:
        async for _ in client.msd.download("boot.iso"):
            pass  # pragma: no cover - the request fails before yielding
    assert info.value.status_code == 400
    assert info.value.error == "MsdOfflineError"
