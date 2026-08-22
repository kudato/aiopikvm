"""Live tests for the endpoints that change something.

`test_readonly.py` reads state and asserts on refusals. Everything here
writes, and it exists because the mocked suite cannot: those 107 assertions
about outgoing requests each encode a *reading* of kvmd's sources, and a
misreading produces a test that agrees with the bug. Three such bugs have
shipped from this repository with the suite green throughout.

So every test here asserts on the state transition the device reports
afterwards, not on the status code. A 200 is what a misspelled parameter
returns too — and on the streamer, a 200 is also what a value outside the
device's own limits returns, right before it is thrown away.

## What it takes to run this

    pytest tests/live --live --live-mutating

plus ``PIKVM_MUTATING_OK`` set to the same URL as ``PIKVM_URL``. Three
further groups need their own variable each, because they reach past kvmd:

``PIKVM_MUTATING_MSD``
    The mass storage lifecycle. Needs a drive that is actually online, which
    means the OTG mass-storage function is switched on — and that attaches a
    USB drive to whatever host the device is wired to.
``PIKVM_MUTATING_GPIO``
    Moves an output away from the state it was found in. On a stock PiKVM v3
    the only output is the USB breaker, so this disconnects the emulated
    keyboard, mouse and drive from the host and plugs them back in.
``PIKVM_MUTATING_LOGOUT``
    kvmd's logout closes **every** session of the user, not the one token it
    was given, so running it drops the browser tabs somebody has open.

## What it never does

`POST /switch/reset` with ``bootloader=1``. It puts a switch unit into
reflashing mode, and that needs an isolated unit and a documented way back
before it is worth exercising at all.

## Restoration

Every test puts back what it changed, in a fixture teardown or a ``finally``
where the assertion could fail first. The streamer is the awkward one: its
parameters are applied about a second after the last write, out of band, and
writing the current value back is dropped rather than queued — see
`test_streamer_params_arrive_about_a_second_late` for what that costs.
"""

import asyncio
import os

import pytest

from aiopikvm import APIError, AuthError, PiKVM
from tests.live.conftest import opt_in

pytestmark = [pytest.mark.live, pytest.mark.mutating]


# The apply-and-restart loop in kvmd waits a second for another write before
# it acts on the batch, then restarts the streamer. Three seconds is that
# plus the restart, with room for a slow device.
STREAMER_SETTLE = 4.0


# ===== ATX


async def test_atx_refuses_every_click_while_it_is_disabled(mutable: PiKVM) -> None:
    """With ``atx.type: disabled`` kvmd refuses rather than doing nothing.

    Worth pinning because the Redfish spelling of the same actions does the
    opposite: `RedfishResource.reset()` answers 204 and drops the command.
    """
    if (await mutable.atx.get_state()).enabled:
        pytest.skip("ATX is enabled on this device; these calls would act on a host")
    calls = [
        mutable.atx.click_power,
        mutable.atx.click_power_long,
        mutable.atx.click_reset,
        mutable.atx.power_on,
        mutable.atx.power_off,
        mutable.atx.power_off_hard,
        mutable.atx.reset_hard,
    ]
    for call in calls:
        with pytest.raises(APIError) as caught:
            await call()
        assert caught.value.status_code == 400
        assert caught.value.error == "AtxDisabledError"


# ===== GPIO


async def test_gpio_rejects_a_channel_that_does_not_exist(mutable: PiKVM) -> None:
    """An unknown channel is a refusal, not a silent drop."""
    with pytest.raises(APIError) as caught:
        await mutable.gpio.switch("aiopikvm-no-such-channel", state=True)
    assert caught.value.status_code == 400
    assert caught.value.error == "GpioChannelNotFoundError"


async def test_gpio_refuses_to_pulse_a_channel_that_cannot(mutable: PiKVM) -> None:
    """``pulse.max_delay`` of zero means the channel has no pulse at all."""
    scheme = (await mutable.gpio.get_state()).model.scheme.outputs
    names = [name for name, out in scheme.items() if out.pulse.max_delay == 0]
    if not names:
        pytest.skip("every output on this device can be pulsed")
    with pytest.raises(APIError) as caught:
        await mutable.gpio.pulse(names[0])
    assert caught.value.status_code == 400
    assert caught.value.error == "GpioPulseNotSupported"


async def _switchable(kvm: PiKVM) -> str:
    """Name an output that can be switched and whose driver is up."""
    state = await kvm.gpio.get_state()
    for name, out in state.model.scheme.outputs.items():
        if out.switch and state.state.outputs[name].online:
            return name
    pytest.skip("no switchable output is online on this device")


async def test_gpio_switch_to_the_current_state_changes_nothing(
    mutable: PiKVM,
) -> None:
    """Writing the state a channel already has leaves it where it was.

    This is the one GPIO write that needs no opt-in: it asks for what is
    already true, so a device wired to anything at all stays where it was.

    ``wait=True`` is not a detail here. Without it kvmd answers as the
    action starts, and a read taken then reports the channel busy — see
    `test_gpio_state_reads_false_while_the_channel_is_busy`, which is what
    the first version of this test walked into.
    """
    name = await _switchable(mutable)
    before = (await mutable.gpio.get_state()).state.outputs[name].state
    await mutable.gpio.switch(name, state=before, wait=True, timeout=30.0)
    after = (await mutable.gpio.get_state()).state.outputs[name]
    assert after.busy is False
    assert after.state is before


async def test_gpio_state_reads_false_while_the_channel_is_busy(
    mutable: PiKVM,
) -> None:
    """``state`` is not the pin while ``busy`` is set — it is ``False``.

    kvmd skips the read entirely for a busy channel and returns the value it
    initialised the pair with, so an output that is on reads as off for the
    whole of any action against it, including one that asked it to stay on.
    Poll ``busy`` first; ``state`` means nothing until it clears.
    """
    name = await _switchable(mutable)
    before = (await mutable.gpio.get_state()).state.outputs[name].state
    if not before:
        pytest.skip("this output is off, so a False reading proves nothing")

    # No `wait`, so kvmd answers while the action is still running.
    await mutable.gpio.switch(name, state=before)
    during = (await mutable.gpio.get_state()).state.outputs[name]
    if not during.busy:
        pytest.skip("the busy window closed before the state could be read")
    assert during.state is False

    for _ in range(30):
        after = (await mutable.gpio.get_state()).state.outputs[name]
        if not after.busy:
            break
        await asyncio.sleep(0.5)
    assert after.busy is False
    assert after.state is before


async def test_gpio_switch_toggles_and_comes_back(mutable: PiKVM) -> None:
    """Move an output, watch kvmd report it, put it back.

    Opt-in on its own variable: on a stock v3 the only output is
    ``__v3_usb_breaker__``, and moving it disconnects the emulated devices
    from the attached host.
    """
    opt_in("PIKVM_MUTATING_GPIO")
    name = await _switchable(mutable)
    before = (await mutable.gpio.get_state()).state.outputs[name].state
    try:
        await mutable.gpio.switch(name, state=not before, wait=True, timeout=30.0)
        assert (await mutable.gpio.get_state()).state.outputs[name].state is (
            not before
        )
    finally:
        await mutable.gpio.switch(name, state=before, wait=True, timeout=30.0)
    assert (await mutable.gpio.get_state()).state.outputs[name].state is before


# ===== MSD


async def test_msd_refuses_every_write_while_the_drive_is_offline(
    mutable: PiKVM,
) -> None:
    """``online: false`` is a refusal on all five writes, and the same one.

    The Redfish insert is the exception, and it is a defect rather than a
    difference: it raises inside kvmd before the check and answers HTTP 500
    with an empty error block. See `tests/live/test_readonly.py`.
    """
    if (await mutable.msd.get_state()).online:
        pytest.skip("the MSD is online; these calls would act on a real drive")

    async def download() -> None:
        async for _chunk in mutable.msd.download("aiopikvm-no-such-image.iso"):
            break

    calls = [
        lambda: mutable.msd.set_params(image="aiopikvm-no-such-image.iso"),
        lambda: mutable.msd.set_connected(True),
        lambda: mutable.msd.remove("aiopikvm-no-such-image.iso"),
        lambda: mutable.msd.upload("aiopikvm-probe.img", b"\x00" * 1024),
        download,
    ]
    for call in calls:
        with pytest.raises(APIError) as caught:
            await call()
        assert caught.value.status_code == 400
        assert caught.value.error == "MsdOfflineError"


async def test_msd_reset_is_accepted_even_with_no_drive(mutable: PiKVM) -> None:
    """``reset`` is how a drive that is not there is brought up, so it cannot
    be gated on the drive being there. It answers 200 either way."""
    before = await mutable.msd.get_state()
    await mutable.msd.reset()
    await asyncio.sleep(2.0)
    after = await mutable.msd.get_state()
    assert after.enabled is before.enabled
    assert after.online is before.online


async def test_msd_image_lifecycle(mutable: PiKVM) -> None:
    """Upload an image, select it, connect the drive, and undo all three.

    Opt-in on its own variable: this needs a drive that is online, which
    means the OTG mass-storage function is on, which means the attached host
    sees a USB drive appear.
    """
    opt_in("PIKVM_MUTATING_MSD")
    state = await mutable.msd.get_state()
    if not state.online:
        pytest.skip("the MSD is offline; nothing here can be exercised")
    if state.drive is not None and state.drive.connected:
        pytest.skip("the drive is connected to the host; refusing to eject it")

    name = "aiopikvm-live-probe.img"
    payload = b"\x00" * (1024 * 1024)
    try:
        upload = await mutable.msd.upload(name, payload)
        stored = upload.name
        assert upload.written == len(payload)

        after_upload = await mutable.msd.get_state()
        assert after_upload.storage is not None
        assert stored in after_upload.storage.images

        await mutable.msd.set_params(image=stored, cdrom=False)
        selected = await mutable.msd.get_state()
        assert selected.drive is not None
        assert selected.drive.image is not None
        assert selected.drive.image.name == stored
        assert selected.drive.connected is False

        await mutable.msd.set_connected(True)
        connected = await mutable.msd.get_state()
        assert connected.drive is not None
        assert connected.drive.connected is True
    finally:
        with_drive = await mutable.msd.get_state()
        if with_drive.drive is not None and with_drive.drive.connected:
            await mutable.msd.set_connected(False)
        current = await mutable.msd.get_state()
        if current.storage is not None and name in current.storage.images:
            await mutable.msd.remove(name)

    end = await mutable.msd.get_state()
    assert end.storage is not None
    assert name not in end.storage.images


# ===== HID


async def test_hid_jiggler_writes_active_and_leaves_enabled_alone(
    mutable: PiKVM,
) -> None:
    """``set_params(jiggler=…)`` moves ``jiggler.active``, not ``enabled``.

    The two read alike and mean different things: ``enabled`` says the
    device was built with a jiggler, ``active`` says it is running. A caller
    who checks the wrong one sees their write do nothing.
    """
    before = (await mutable.hid.get_state()).jiggler
    assert before is not None
    try:
        await mutable.hid.set_params(jiggler=not before.active)
        mid = (await mutable.hid.get_state()).jiggler
        assert mid is not None
        assert mid.active is (not before.active)
        assert mid.enabled is before.enabled
    finally:
        await mutable.hid.set_params(jiggler=before.active)
    end = (await mutable.hid.get_state()).jiggler
    assert end is not None
    assert end.active is before.active


async def test_hid_rejects_a_key_name_it_does_not_know(mutable: PiKVM) -> None:
    """Over HTTP a wrong key name is a refusal. Over the WebSocket the same
    name is dropped inside kvmd's handler with no answer at all."""
    with pytest.raises(APIError) as caught:
        await mutable.hid.send_key("AiopikvmNoSuchKey")
    assert caught.value.status_code == 400
    assert caught.value.error == "ValidatorError"


async def test_sending_a_key_resets_the_inactivity_counter(mutable: PiKVM) -> None:
    """kvmd's own proof that the key was accepted, and the only one that does
    not depend on a host being there to act on it.

    ``CapsLock`` is the key because it is the least destructive one there is:
    it changes a modifier state and types nothing.
    """
    before = await mutable.hid.get_inactivity()
    if before < 5:
        pytest.skip("somebody is using this device right now")
    await mutable.hid.send_key("CapsLock")
    await mutable.hid.send_key("CapsLock")
    await asyncio.sleep(1.0)
    assert await mutable.hid.get_inactivity() < before


async def test_capslock_reaches_the_host_and_the_led_comes_back(
    mutable: PiKVM,
) -> None:
    """The whole path end to end: this client sends a key, the attached host
    acts on it, and kvmd reports the LED the host set.

    Nothing else in the suite proves the emulated keyboard is wired to
    anything. Skipped rather than failed when the LED does not move, because
    a host that is powered off is not a gap in this client.
    """
    keyboard = (await mutable.hid.get_state()).keyboard
    if not keyboard.online:
        pytest.skip("the emulated keyboard is offline")
    before = keyboard.leds.caps
    try:
        await mutable.hid.send_key("CapsLock")
        await asyncio.sleep(1.5)
        mid = (await mutable.hid.get_state()).keyboard.leds.caps
        if mid is before:
            pytest.skip(
                "the caps LED did not move; the attached host is not acting "
                "on the emulated keyboard"
            )
        assert mid is (not before)
    finally:
        await mutable.hid.send_key("CapsLock")
        await asyncio.sleep(1.5)
    assert (await mutable.hid.get_state()).keyboard.leds.caps is before


async def test_hid_reset_leaves_both_devices_online(mutable: PiKVM) -> None:
    """A reset re-initialises the HID and comes back."""
    await mutable.hid.reset()
    await asyncio.sleep(2.0)
    state = await mutable.hid.get_state()
    assert state.keyboard.online is True
    assert state.mouse.online is True


# ===== streamer


@pytest.fixture()
async def streamer_params(mutable: PiKVM):
    """Restore the streamer parameters, and make sure the restore landed.

    Putting them back is not a matter of writing the old values: kvmd drops
    a write whose value equals what the running streamer already has, so the
    restore has to be read back rather than assumed.
    """
    before = (await mutable.streamer.get_state()).params
    yield before
    fields = before.model_dump(exclude_none=True)
    await mutable.streamer.set_params(**fields)
    await asyncio.sleep(STREAMER_SETTLE)
    after = (await mutable.streamer.get_state()).params
    assert after.model_dump(exclude_none=True) == fields


async def test_streamer_params_arrive_about_a_second_late(
    mutable: PiKVM, streamer_params
) -> None:
    """``set_params`` answers 200 before it has done anything.

    kvmd puts the values in a pending batch, waits a second for another
    write to join it, then applies the batch and restarts the streamer. Read
    immediately afterwards, ``params`` still holds the old value — and a
    write of that old value in the meantime is compared against the running
    streamer, found equal, and dropped, so it does not cancel anything.
    """
    if not (await mutable.streamer.get_state()).features.quality:
        pytest.skip("this device has no adjustable quality")
    assert streamer_params.quality is not None
    other = 55 if streamer_params.quality != 55 else 65

    await mutable.streamer.set_params(quality=other)
    assert (
        await mutable.streamer.get_state()
    ).params.quality == streamer_params.quality

    await asyncio.sleep(STREAMER_SETTLE)
    assert (await mutable.streamer.get_state()).params.quality == other


async def test_streamer_drops_a_value_outside_its_own_limits(
    mutable: PiKVM, streamer_params
) -> None:
    """``limits`` is enforced where nothing can see it.

    kvmd's HTTP validator takes a far wider range than the device advertises
    — 0 to 120 frames per second against a limit of 10 to 70 here — so the
    call is accepted, queued, and then discarded by the streamer when the
    batch is applied. Nothing anywhere says so.
    """
    state = await mutable.streamer.get_state()
    floor = state.limits.desired_fps.min
    if floor <= 0:
        pytest.skip("this device sets no lower bound on desired_fps")
    await mutable.streamer.set_params(desired_fps=floor - 1)
    await asyncio.sleep(STREAMER_SETTLE)
    assert (
        await mutable.streamer.get_state()
    ).params.desired_fps == streamer_params.desired_fps


async def test_streamer_rejects_a_quality_outside_the_validator(
    mutable: PiKVM,
) -> None:
    """The other half: 1 to 100 *is* checked, and checked at the door."""
    with pytest.raises(APIError) as caught:
        await mutable.streamer.set_params(quality=999)
    assert caught.value.status_code == 400
    assert caught.value.error == "ValidatorError"


async def test_streamer_refuses_a_resolution_it_cannot_change(
    mutable: PiKVM,
) -> None:
    """A parameter the capture path does not have is a named refusal."""
    if (await mutable.streamer.get_state()).features.resolution:
        pytest.skip("this device can change resolution")
    with pytest.raises(APIError) as caught:
        await mutable.streamer.set_params(resolution="1920x1080")
    assert caught.value.status_code == 400
    assert caught.value.error == "StreamerResolutionNotSupported"


async def test_snapshot_and_ocr_need_somebody_watching(mutable: PiKVM) -> None:
    """kvmd runs ustreamer only while a client is connected.

    With nobody watching, both answer 503 — ``allow_offline`` does not help,
    because it is about the *video source* being offline, not the streamer
    process. One socket covers both halves: opening more of them is what
    makes kvmd unstable, so this asserts the refusal and the success in the
    same test rather than in two.
    """
    if (await mutable.streamer.get_state()).streamer is not None:
        pytest.skip("something is already watching this device's stream")

    for call in (mutable.streamer.snapshot, mutable.streamer.ocr):
        with pytest.raises(APIError) as caught:
            await call()
        assert caught.value.status_code == 503

    async with mutable.ws(stream=True):
        await asyncio.sleep(STREAMER_SETTLE)
        assert (await mutable.streamer.get_state()).streamer is not None

        image = await mutable.streamer.snapshot()
        assert image.data[:3] == b"\xff\xd8\xff"

        text = await mutable.streamer.ocr()
        assert isinstance(text, str)

        with pytest.raises(APIError) as caught:
            await mutable.streamer.ocr(langs=["aiopikvm-no-such-lang"])
        assert caught.value.status_code == 400
        assert caught.value.error == "ValidatorError"


async def test_delete_snapshot_clears_the_saved_one(mutable: PiKVM) -> None:
    """Deleting a snapshot that is not there is not an error, and the state
    says ``saved: null`` either way."""
    await mutable.streamer.delete_snapshot()
    assert (await mutable.streamer.get_state()).snapshot.saved is None


# ===== switch


async def _no_switch(kvm: PiKVM) -> bool:
    """Whether this device has no switch unit attached."""
    return not (await kvm.switch.get_state()).model.units


async def test_switch_takes_port_commands_with_no_unit_attached(
    mutable: PiKVM,
) -> None:
    """Every port command answers 200 on a device with no switch.

    kvmd validates the shape of the argument and then hands the command to a
    chain that has nowhere to send it. There is no error, and no way to tell
    this apart from a command that landed — which is why
    `SwitchState.model.units` is worth reading first.
    """
    if not await _no_switch(mutable):
        pytest.skip("a switch is attached; these calls would act on real ports")
    await mutable.switch.set_active(0)
    await mutable.switch.set_active_prev()
    await mutable.switch.set_active_next()
    await mutable.switch.set_beacon(True, port=0)
    await mutable.switch.set_beacon(True, uplink=0)
    await mutable.switch.reset(0)
    await mutable.switch.atx_power(0, "on")
    await mutable.switch.atx_click(0, "power")
    assert await _no_switch(mutable)


async def test_switch_port_name_is_stored_where_nothing_reads_it(
    mutable: PiKVM,
) -> None:
    """``set_port_params`` persists to the device with no unit attached.

    The name goes into kvmd's own storage and survives a restart, but the
    state exposes port names only inside the port list — which is empty
    here. So it is a write with no read, and the only honest assertion is
    that nothing observable moved. Restored to the empty string, which is
    the default kvmd stores by deleting the entry.
    """
    if not await _no_switch(mutable):
        pytest.skip("a switch is attached; this would rename a real port")
    before = await mutable.switch.get_state()
    try:
        await mutable.switch.set_port_params(0, name="aiopikvm-probe")
        assert (await mutable.switch.get_state()).model.ports == before.model.ports
    finally:
        await mutable.switch.set_port_params(0, name="")


async def test_switch_colors_round_trip(mutable: PiKVM) -> None:
    """Colours are device-wide and readable, so this one really is a
    transition: write, read it back, ask for the built-in value again."""
    before = (await mutable.switch.get_state()).colors
    try:
        await mutable.switch.set_colors(active="0000FF:40:0000")
        mid = (await mutable.switch.get_state()).colors.active
        assert (mid.red, mid.green, mid.blue) == (0, 0, 255)
        assert mid.brightness == 0x40
    finally:
        await mutable.switch.set_colors(active="default")
    assert (await mutable.switch.get_state()).colors == before


async def test_switch_refuses_a_malformed_colour(mutable: PiKVM) -> None:
    """The format is ``RRGGBB:BB:IIII``, and every field is fixed-width."""
    with pytest.raises(APIError) as caught:
        await mutable.switch.set_colors(active="00FF00:64:0")
    assert caught.value.status_code == 400
    assert caught.value.error == "ValidatorError"


async def test_switch_edid_round_trip(mutable: PiKVM) -> None:
    """Create, rename and remove a stored EDID.

    This half of the switch API is storage rather than hardware, so it works
    on a device with no unit attached — and it is the only switch write
    whose effect can be read straight back.
    """
    before = await mutable.switch.get_edids()
    edid_id = await mutable.switch.create_edid("aiopikvm-probe", before["default"].data)
    try:
        assert edid_id not in before
        created = await mutable.switch.get_edids()
        assert created[edid_id].name == "aiopikvm-probe"
        assert created[edid_id].data == before["default"].data

        await mutable.switch.change_edid(edid_id, name="aiopikvm-probe-renamed")
        assert (await mutable.switch.get_edids())[edid_id].name == (
            "aiopikvm-probe-renamed"
        )
    finally:
        await mutable.switch.remove_edid(edid_id)
    assert sorted(await mutable.switch.get_edids()) == sorted(before)


async def test_switch_refuses_to_touch_the_built_in_edid(mutable: PiKVM) -> None:
    """``default`` is not an id the validator accepts, so it cannot be
    removed or edited by name — the refusal comes before any handler."""
    with pytest.raises(APIError) as caught:
        await mutable.switch.remove_edid("default")
    assert caught.value.status_code == 400
    assert caught.value.error == "ValidatorError"


async def test_switch_refuses_malformed_edid_data(mutable: PiKVM) -> None:
    """EDID data is hex of a fixed length, checked at the door."""
    with pytest.raises(APIError) as caught:
        await mutable.switch.create_edid("aiopikvm-bad", "zz")
    assert caught.value.status_code == 400
    assert caught.value.error == "ValidatorError"


# ===== auth


def _credentials() -> tuple[str, str, str | None]:
    """The device credentials, as `login()` takes them."""
    return (
        os.environ.get("PIKVM_USER", "admin"),
        os.environ["PIKVM_PASSWD"],
        os.environ.get("PIKVM_TOTP"),
    )


async def test_login_returns_a_token_that_check_accepts(mutable: PiKVM) -> None:
    """A session is opened, stored in the jar, and then used.

    It is left open. kvmd has no way to close one session — only every
    session of the user — so the tidying up is
    `test_logout_closes_every_session`, and that is opt-in for the same
    reason. A session costs a dictionary entry in kvmd's memory and is gone
    when it restarts.
    """
    token = await mutable.auth.login(*_credentials())
    assert len(token) == 64
    assert int(token, 16) >= 0
    assert mutable.cookies.get("auth_token") == token
    await mutable.auth.check()


async def test_login_refuses_a_wrong_password(mutable: PiKVM) -> None:
    """A refusal, and specifically an `AuthError` rather than a plain 400."""
    with pytest.raises(AuthError) as caught:
        await mutable.auth.login(
            os.environ.get("PIKVM_USER", "admin"), "aiopikvm-definitely-not-it"
        )
    assert caught.value.status_code == 403


async def test_logout_closes_every_session(mutable: PiKVM) -> None:
    """kvmd's logout takes one token and closes every session of that
    token's user, browser tabs included. That is why it is opt-in.

    Proving it needs a client that cannot paper over a dead token. In the
    default ``headers`` mode the credentials go out on every request and a
    closed session changes nothing; in ``cookie`` mode the client answers a
    refused token by logging in again. So the witness here is a cookie-mode
    client whose *own* password is wrong: while the token it was handed is
    alive it never logs in, and the moment that token dies the re-login
    fails and says so.
    """
    opt_in("PIKVM_MUTATING_LOGOUT")
    user, passwd, totp = _credentials()

    first = await mutable.auth.login(user, passwd, totp)
    second = await mutable.auth.login(user, passwd, totp)
    assert first != second

    async with PiKVM(
        os.environ["PIKVM_URL"],
        user=user,
        passwd="aiopikvm-definitely-not-it",
        auth="cookie",
    ) as witness:
        witness.cookies.set("auth_token", first)
        await witness.auth.check()

        await mutable.auth.logout()

        witness.cookies.set("auth_token", first)
        with pytest.raises(AuthError):
            await witness.auth.check()

        witness.cookies.set("auth_token", second)
        with pytest.raises(AuthError):
            await witness.auth.check()

    # The client that did the logging out is unaffected: nothing it sends
    # depends on a session.
    await mutable.auth.check()
