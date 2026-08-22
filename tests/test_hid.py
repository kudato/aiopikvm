"""HIDResource tests."""

import httpx
import pytest
import respx

from aiopikvm import ConfigurationError, PiKVM
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
    # Read the expected value off the capture rather than pinning the
    # seconds the device happened to be idle at, which every re-capture
    # changes and no version of the client can influence.
    body = load_json("hid_inactivity")
    mock_api.get("/api/hid/inactivity").mock(
        return_value=httpx.Response(200, json=body)
    )
    assert await client.hid.get_inactivity() == body["result"]["inactivity"]


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
    assert dict(mock_api.calls[-1].request.url.params) == {"key": "KeyA", "state": "1"}


async def test_send_key_can_ask_kvmd_to_release_it(
    mock_api: respx.MockRouter, client: PiKVM
) -> None:
    """One event that presses and releases, so a death mid-script holds no key.

    kvmd sends the release itself, straight after the press and before it
    reads anything else — which is the one keystroke a lost connection
    cannot interrupt halfway (#74).
    """
    mock_api.post("/api/hid/events/send_key").mock(
        return_value=httpx.Response(200, json=OK)
    )
    await client.hid.send_key("KeyA", state=True, finish=True)
    assert dict(mock_api.calls[-1].request.url.params) == {
        "key": "KeyA",
        "state": "1",
        "finish": "1",
    }


@pytest.mark.parametrize("finish", [None, False])
async def test_send_key_says_nothing_about_finish_unless_asked(
    finish: bool | None, mock_api: respx.MockRouter, client: PiKVM
) -> None:
    """The default event is the one a client that never heard of it sends.

    ``None`` and ``False`` are the same event on a press: kvmd reads the
    parameter as false when it is absent, so naming it would say what
    leaving it out already says.
    """
    mock_api.post("/api/hid/events/send_key").mock(
        return_value=httpx.Response(200, json=OK)
    )
    await client.hid.send_key("KeyA", state=True, finish=finish)
    assert dict(mock_api.calls[-1].request.url.params) == {"key": "KeyA", "state": "1"}


@pytest.mark.parametrize(
    ("state", "expected"),
    [(None, {"key": "KeyA"}), (False, {"key": "KeyA", "state": "0"})],
)
async def test_send_key_leaves_finish_out_where_kvmd_would_not_act_on_it(
    state: bool | None,
    expected: dict[str, str],
    mock_api: respx.MockRouter,
    client: PiKVM,
) -> None:
    """Only a press carries the flag (#74).

    kvmd's handler reads ``finish`` inside the branch that reads ``state``,
    and its ``send_key_event`` acts on it only when that state is a press.
    Without a state the other branch runs, which passes its own
    ``finish=True`` and never looks at the query; on a release the flag is
    parsed and dropped. Sending it either way would name something kvmd does
    nothing with.
    """
    mock_api.post("/api/hid/events/send_key").mock(
        return_value=httpx.Response(200, json=OK)
    )
    await client.hid.send_key("KeyA", state=state, finish=True)
    assert dict(mock_api.calls[-1].request.url.params) == expected


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
    with pytest.raises(ConfigurationError, match="at least one key"):
        await client.hid.send_shortcut()


@pytest.mark.parametrize(
    "key", ["", "Control Left", "ControlLeft,KeyC", "\t", "\n", "\xa0", "KeyA\r"]
)
async def test_send_shortcut_refuses_a_key_kvmd_would_split(
    mock_api: respx.MockRouter, client: PiKVM, key: str
) -> None:
    # kvmd takes `keys` as one string, strips it, splits it on [,\t ]+ and
    # drops what comes out empty. The strip is why every whitespace character
    # counts and not just the two in the split: a "\n" key at either end of
    # the shortcut is trimmed away as surely as an empty one, and the rest is
    # pressed with nothing said about the key that vanished.
    with pytest.raises(ConfigurationError, match="cannot be part of a shortcut"):
        await client.hid.send_shortcut("ControlLeft", key)
    # No route is registered: a request escaping the check would fail here.
    assert not mock_api.calls, "nothing may go out once a key is refused"


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
    # kvmd reads the flag through its bool validator, which takes 1/0 among
    # other spellings — and refuses the request if the parameter is missing.
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
