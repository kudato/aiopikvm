"""HIDResource tests."""

import httpx
import pytest
import respx

from aiopikvm import PiKVM
from tests.fixtures import load_json

OK = {"ok": True, "result": {}}


async def test_get_state(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.get("/api/hid").mock(
        return_value=httpx.Response(200, json=load_json("hid"))
    )
    state = await client.hid.get_state()
    assert state.enabled is True
    assert state.online is True
    assert state.connected is None
    assert state.keyboard.online is True
    assert state.keyboard.leds.caps is False
    assert state.mouse.absolute is True
    assert state.mouse.outputs.active == "usb"
    assert state.mouse.outputs.available == ["usb", "usb_rel"]
    assert state.jiggler.enabled is True
    assert state.jiggler.interval == 60
    # The OTG keyboard cannot switch modes, so kvmd offers no choice for it
    # even though the mouse on the same device has two.
    assert state.keyboard.outputs.available == []
    assert state.keyboard.outputs.active == ""


async def test_get_inactivity(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.get("/api/hid/inactivity").mock(
        return_value=httpx.Response(200, json=load_json("hid_inactivity"))
    )
    assert await client.hid.get_inactivity() == 239


async def test_type_text(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/hid/print").mock(return_value=httpx.Response(200, json=OK))
    await client.hid.type_text("hello world")
    request = mock_api.calls[-1].request
    assert request.content == b"hello world"


async def test_type_text_sends_limit_zero_by_default(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    # kvmd defaults `limit` to 1024 and truncates the body; omitting the param
    # is what used to lose everything past the first 1024 characters.
    mock_api.post("/api/hid/print").mock(return_value=httpx.Response(200, json=OK))
    await client.hid.type_text("x" * 2000)
    assert mock_api.calls[-1].request.url.params["limit"] == "0"


async def test_type_text_with_slow(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/hid/print").mock(return_value=httpx.Response(200, json=OK))
    await client.hid.type_text("hello", slow=True)
    assert mock_api.calls[-1].request.url.params["slow"] == "1"


async def test_type_text_with_limit(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/hid/print").mock(return_value=httpx.Response(200, json=OK))
    await client.hid.type_text("hello", limit=10)
    assert mock_api.calls[-1].request.url.params["limit"] == "10"


async def test_type_text_with_keymap(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/hid/print").mock(return_value=httpx.Response(200, json=OK))
    await client.hid.type_text("hello", keymap="en-us")
    assert mock_api.calls[-1].request.url.params["keymap"] == "en-us"


async def test_type_text_with_delay(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/hid/print").mock(return_value=httpx.Response(200, json=OK))
    await client.hid.type_text("hello", delay=0.05)
    assert mock_api.calls[-1].request.url.params["delay"] == "0.05"


async def test_type_text_omits_unset_params(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    mock_api.post("/api/hid/print").mock(return_value=httpx.Response(200, json=OK))
    await client.hid.type_text("hello")
    params = mock_api.calls[-1].request.url.params
    assert set(params) == {"limit"}


async def test_send_key(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/hid/events/send_key").mock(
        return_value=httpx.Response(200, json=OK)
    )
    await client.hid.send_key("KeyA", state=True)
    request = mock_api.calls[-1].request
    assert "key=KeyA" in str(request.url)


async def test_send_shortcut(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/hid/events/send_shortcut").mock(
        return_value=httpx.Response(200, json=OK)
    )
    await client.hid.send_shortcut("ControlLeft", "KeyC")
    request = mock_api.calls[-1].request
    # PiKVM reads `keys` as a single comma-separated value; repeated
    # params (keys=A&keys=B) would drop all keys except the first.
    assert request.url.params.get_list("keys") == ["ControlLeft,KeyC"]


async def test_send_shortcut_no_keys(mock_api: respx.MockRouter, client: PiKVM) -> None:
    with pytest.raises(ValueError, match="at least one key"):
        await client.hid.send_shortcut()


async def test_send_mouse_move(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/hid/events/send_mouse_move").mock(
        return_value=httpx.Response(200, json=OK)
    )
    await client.hid.send_mouse_move(100, 200)
    request = mock_api.calls[-1].request
    assert "to_x=100" in str(request.url)
    assert "to_y=200" in str(request.url)


async def test_send_mouse_button(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/hid/events/send_mouse_button").mock(
        return_value=httpx.Response(200, json=OK)
    )
    await client.hid.send_mouse_button("left", state=True)
    request = mock_api.calls[-1].request
    assert "button=left" in str(request.url)
    assert "state=1" in str(request.url)


async def test_send_mouse_wheel(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/hid/events/send_mouse_wheel").mock(
        return_value=httpx.Response(200, json=OK)
    )
    await client.hid.send_mouse_wheel(0, -5)
    request = mock_api.calls[-1].request
    assert "delta_x=0" in str(request.url)
    assert "delta_y=-5" in str(request.url)


async def test_send_mouse_relative(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/hid/events/send_mouse_relative").mock(
        return_value=httpx.Response(200, json=OK)
    )
    await client.hid.send_mouse_relative(10, -10)
    request = mock_api.calls[-1].request
    assert "delta_x=10" in str(request.url)
    assert "delta_y=-10" in str(request.url)


async def test_reset(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/hid/reset").mock(return_value=httpx.Response(200, json=OK))
    await client.hid.reset()


@pytest.mark.parametrize(("connected", "sent"), [(True, "1"), (False, "0")])
async def test_set_connected(
    mock_api: respx.MockRouter, client: PiKVM, connected: bool, sent: str
) -> None:
    # kvmd reads the flag through its bool validator, which takes 1/0 — and
    # rejects the request outright if the parameter is missing.
    mock_api.post("/api/hid/set_connected").mock(
        return_value=httpx.Response(200, json=OK)
    )
    await client.hid.set_connected(connected)
    assert mock_api.calls[-1].request.url.params["connected"] == sent


async def test_set_params(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/hid/set_params").mock(return_value=httpx.Response(200, json=OK))
    await client.hid.set_params(keyboard_output="usb", mouse_output="usb_rel")
    request = mock_api.calls[-1].request
    assert "keyboard_output=usb" in str(request.url)
    assert "mouse_output=usb_rel" in str(request.url)


async def test_set_params_partial(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/hid/set_params").mock(return_value=httpx.Response(200, json=OK))
    await client.hid.set_params(keyboard_output="usb")
    request = mock_api.calls[-1].request
    assert "keyboard_output=usb" in str(request.url)
    assert "mouse_output" not in str(request.url)


@pytest.mark.parametrize(("jiggler", "expected"), [(True, "1"), (False, "0")])
async def test_set_params_jiggler(
    mock_api: respx.MockRouter, client: PiKVM, jiggler: bool, expected: str
) -> None:
    mock_api.post("/api/hid/set_params").mock(return_value=httpx.Response(200, json=OK))
    await client.hid.set_params(jiggler=jiggler)
    assert mock_api.calls[-1].request.url.params["jiggler"] == expected


async def test_get_keymaps(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.get("/api/hid/keymaps").mock(
        return_value=httpx.Response(200, json=load_json("hid_keymaps"))
    )
    keymaps = await client.hid.get_keymaps()
    assert keymaps.default == "ru"
    assert "en-us" in keymaps.available


async def test_send_shortcut_multiple_keys(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    mock_api.post("/api/hid/events/send_shortcut").mock(
        return_value=httpx.Response(200, json=OK)
    )
    await client.hid.send_shortcut("ControlLeft", "AltLeft", "Delete")
    request = mock_api.calls[-1].request
    assert request.url.params.get_list("keys") == ["ControlLeft,AltLeft,Delete"]
