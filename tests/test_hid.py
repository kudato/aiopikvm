"""HIDResource tests."""

import httpx
import respx

from aiopikvm import PiKVM

HID_STATE = {
    "ok": True,
    "result": {
        "online": True,
        "busy": False,
        "connected": None,
        "keyboard": {"connected": True},
        "mouse": {"absolute": True, "connected": True},
    },
}


async def test_get_state(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.get("/api/hid").mock(return_value=httpx.Response(200, json=HID_STATE))
    state = await client.hid.get_state()
    assert state.online is True
    assert state.keyboard.connected is True
    assert state.mouse.absolute is True


async def test_type_text(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/hid/print").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    await client.hid.type_text("hello world")
    request = mock_api.calls[-1].request
    assert request.content == b"hello world"


async def test_send_key(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/hid/events/send_key").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    await client.hid.send_key("KeyA", state=True)
    request = mock_api.calls[-1].request
    assert "key=KeyA" in str(request.url)


async def test_send_shortcut(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/hid/events/send_shortcut").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    await client.hid.send_shortcut("ControlLeft", "KeyC")
    request = mock_api.calls[-1].request
    url_params = str(request.url)
    assert "key=ControlLeft" in url_params
    assert "key=KeyC" in url_params


async def test_send_mouse_move(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/hid/events/send_mouse_move").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    await client.hid.send_mouse_move(100, 200)
    request = mock_api.calls[-1].request
    assert "to_x=100" in str(request.url)
    assert "to_y=200" in str(request.url)


async def test_send_mouse_button(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/hid/events/send_mouse_button").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    await client.hid.send_mouse_button("left", state=True)
    request = mock_api.calls[-1].request
    assert "button=left" in str(request.url)
    assert "state=1" in str(request.url)


async def test_send_mouse_wheel(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/hid/events/send_mouse_wheel").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    await client.hid.send_mouse_wheel(0, -5)
    request = mock_api.calls[-1].request
    assert "delta_x=0" in str(request.url)
    assert "delta_y=-5" in str(request.url)


async def test_send_mouse_relative(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/hid/events/send_mouse_relative").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    await client.hid.send_mouse_relative(10, -10)
    request = mock_api.calls[-1].request
    assert "delta_x=10" in str(request.url)
    assert "delta_y=-10" in str(request.url)


async def test_reset(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/hid/reset").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    await client.hid.reset()


async def test_set_connected(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/hid/set_connected").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    await client.hid.set_connected(True)


async def test_set_params(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/hid/set_params").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    await client.hid.set_params(keyboard_output="usb", mouse_output="usb_rel")
    request = mock_api.calls[-1].request
    assert "keyboard_output=usb" in str(request.url)
    assert "mouse_output=usb_rel" in str(request.url)


async def test_set_params_partial(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/hid/set_params").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    await client.hid.set_params(keyboard_output="usb")
    request = mock_api.calls[-1].request
    assert "keyboard_output=usb" in str(request.url)
    assert "mouse_output" not in str(request.url)


async def test_type_text_with_limit(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.post("/api/hid/print").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    await client.hid.type_text("hello", limit=10)
    request = mock_api.calls[-1].request
    assert "limit=10" in str(request.url)


async def test_get_keymaps(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.get("/api/hid/keymaps").mock(
        return_value=httpx.Response(
            200, json={"ok": True, "result": {"keymaps": {"en": {}}}}
        )
    )
    result = await client.hid.get_keymaps()
    assert "keymaps" in result


async def test_send_shortcut_with_wait(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    mock_api.post("/api/hid/events/send_shortcut").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {}})
    )
    await client.hid.send_shortcut("KeyA", "KeyB", wait=100)
    request = mock_api.calls[-1].request
    assert "wait=100" in str(request.url)
