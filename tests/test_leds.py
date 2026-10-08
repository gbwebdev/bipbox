"""The LED state machine, exercised against fake hardware.

These are the behaviours that are invisible until someone is standing in front
of the box: whether a pattern actually holds for the duration written down,
whether activity outranks state, and whether the telegraphy lamp's inverted
logic survives.
"""

import pytest
from bipboxd.calibration import Calibration
from bipboxd.hardware import FakeHardware
from bipboxd.leds import (
    PATTERNS,
    TICK_MS,
    Lamp,
    LedController,
    Service,
    pattern_for,
    pattern_level,
)


class Clock:
    """A clock the test drives, so timing assertions are exact."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance_ms(self, ms):
        self.now += ms / 1000


@pytest.fixture
def rig():
    clock = Clock()
    hardware = FakeHardware(clock=clock)
    controller = LedController(hardware, Calibration(), clock=clock)
    return controller, hardware, clock


# ── Patterns ─────────────────────────────────────────────────────────────────


def test_every_timing_is_a_multiple_of_the_tick():
    """Otherwise a phase is quantised and does not last as long as documented."""
    for name, phases in PATTERNS.items():
        for on, off in phases:
            assert on % TICK_MS == 0, f"{name}: on={on}"
            assert off % TICK_MS == 0, f"{name}: off={off}"


def test_steady_patterns_do_not_blink():
    assert all(pattern_level("on", t) == 1 for t in range(0, 5000, 25))
    assert all(pattern_level("off", t) == 0 for t in range(0, 5000, 25))


def test_slow_is_lit_for_its_first_phase_and_dark_for_its_second():
    assert pattern_level("slow", 0) == 1
    assert pattern_level("slow", 549) == 1
    assert pattern_level("slow", 550) == 0
    assert pattern_level("slow", 1099) == 0
    assert pattern_level("slow", 1100) == 1  # cycle repeats


def test_heartbeat_is_a_brief_blink_on_a_three_second_cycle():
    assert pattern_level("heartbeat", 0) == 1
    assert pattern_level("heartbeat", 199) == 1
    assert pattern_level("heartbeat", 200) == 0
    assert pattern_level("heartbeat", 2999) == 0
    assert pattern_level("heartbeat", 3000) == 1


def test_ap_pattern_is_short_short_long():
    """ ". . _" repeating, with no trailing pause (Q30)."""
    lit_spans = []
    current = None
    for t in range(0, 1500, 25):
        level = pattern_level("ap", t)
        if level and current is None:
            current = t
        elif not level and current is not None:
            lit_spans.append(t - current)
            current = None
    assert lit_spans == [200, 200, 500]


def test_fast_stays_distinguishable_from_slow():
    """They share a lamp at different times, so the ratio is a requirement."""
    assert sum(PATTERNS["slow"][0]) >= 2.5 * sum(PATTERNS["fast"][0])


# ── State mapping ────────────────────────────────────────────────────────────


def test_wifi_uses_the_ap_pattern_only_in_ap_mode():
    assert pattern_for(Lamp.WIFI, Service.AP_MODE) == "ap"
    assert pattern_for(Lamp.WIFI, Service.CONNECTED) == "on"
    assert pattern_for(Lamp.WIFI, Service.DOWN) == "off"


def test_telegraphy_is_inverted_relative_to_the_others():
    """Dark means healthy; the heartbeat means the client is down."""
    assert pattern_for(Lamp.TELEGRAPHY, Service.CONNECTED) == "off"
    assert pattern_for(Lamp.TELEGRAPHY, Service.DOWN) == "heartbeat"
    for lamp in (Lamp.TELEX, Lamp.VOIP, Lamp.WIFI):
        assert pattern_for(lamp, Service.CONNECTED) == "on"
        assert pattern_for(lamp, Service.DOWN) == "off"


# ── Controller ───────────────────────────────────────────────────────────────


def test_lamps_map_to_the_calibrated_bits(rig):
    controller, hardware, _ = rig
    controller.set_state(Lamp.TELEX, Service.CONNECTED)
    controller.tick()

    assert hardware.is_lit(4)  # box A: telex is QE
    assert not hardware.is_lit(3)  # voip is QD and is still down


def test_a_reversed_calibration_moves_the_lamp(rig):
    """Proves the mapping is honoured rather than hard-coded."""
    _, _, clock = rig
    hardware = FakeHardware(clock=clock)
    swapped = Calibration(led_bits={"telegraphy": 2, "telex": 3, "voip": 4, "wifi": 5})
    controller = LedController(hardware, swapped, clock=clock)

    controller.set_state(Lamp.TELEX, Service.CONNECTED)
    controller.tick()

    assert hardware.is_lit(3)
    assert not hardware.is_lit(4)


def test_all_lamps_start_down(rig):
    controller, hardware, _ = rig
    controller.tick()
    # Telegraphy's "down" is a heartbeat, which begins lit.
    assert hardware.byte == 1 << 2


def test_activity_overrides_state_then_falls_back(rig):
    controller, hardware, clock = rig
    controller.set_state(Lamp.TELEX, Service.CONNECTED)
    bit = Calibration().bit("telex")
    controller.flash(Lamp.TELEX)

    # While flashing, the lamp blinks fast instead of sitting steady.
    levels = set()
    for _ in range(int(350 / TICK_MS)):
        controller.tick()
        levels.add(hardware.is_lit(bit))
        clock.advance_ms(TICK_MS)
    assert levels == {True, False}

    # Once the flash expires it returns to steady-on (Q31).
    clock.advance_ms(200)
    for _ in range(8):
        controller.tick()
        assert hardware.is_lit(bit)
        clock.advance_ms(TICK_MS)


def test_a_flash_opens_with_a_gap_so_it_shows_on_a_steady_lamp(rig):
    """On a steady-on lamp, only darkness is visible — a lit flash shows nothing."""
    controller, hardware, clock = rig
    controller.set_state(Lamp.TELEX, Service.CONNECTED)
    bit = Calibration().bit("telex")

    controller.tick()
    assert hardware.is_lit(bit)  # steady before

    controller.flash(Lamp.TELEX)
    controller.tick()
    assert not hardware.is_lit(bit)  # the very next frame is dark


def test_a_flash_lasts_at_least_one_full_fast_cycle(rig):
    """Shorter and the blink can be swallowed by the pattern's phase."""
    from bipboxd.leds import FLASH_HOLD_MS

    assert sum(PATTERNS["fast"][0]) <= FLASH_HOLD_MS


def test_repeated_flashes_extend_without_restarting_the_phase(rig):
    """Sustained activity should read as continuous blinking, not a stutter."""
    controller, hardware, clock = rig
    controller.set_state(Lamp.VOIP, Service.CONNECTED)
    bit = Calibration().bit("voip")

    levels = set()
    for _ in range(40):
        controller.flash(Lamp.VOIP)  # as audio frames keep arriving
        controller.tick()
        levels.add(hardware.is_lit(bit))
        clock.advance_ms(TICK_MS)

    assert levels == {True, False}


def test_forced_on_outranks_both_state_and_activity(rig):
    """Someone else holding their telegraphy key is an event, not a state."""
    controller, hardware, clock = rig
    bit = Calibration().bit("telegraphy")
    controller.set_state(Lamp.TELEGRAPHY, Service.CONNECTED)
    controller.set_forced_on(Lamp.TELEGRAPHY, True)

    for _ in range(40):
        controller.tick()
        assert hardware.is_lit(bit)
        clock.advance_ms(25)

    controller.set_forced_on(Lamp.TELEGRAPHY, False)
    controller.tick()
    assert not hardware.is_lit(bit)


def test_the_byte_is_only_written_when_it_changes(rig):
    """40 writes a second to an unchanged register would be pure waste."""
    controller, hardware, clock = rig
    # Every lamp steady: telegraphy's CONNECTED is dark, the rest are lit.
    for lamp in Lamp:
        controller.set_state(lamp, Service.CONNECTED)

    for _ in range(40):
        controller.tick()
        clock.advance_ms(TICK_MS)

    assert len(hardware.writes) == 1


def test_a_blinking_lamp_does_write_every_transition(rig):
    controller, hardware, clock = rig
    for lamp in Lamp:
        controller.set_state(lamp, Service.CONNECTED)
    controller.set_state(Lamp.WIFI, Service.SEARCHING)  # slow blink

    for _ in range(int(2200 / TICK_MS)):
        controller.tick()
        clock.advance_ms(TICK_MS)

    # Two full 1100 ms cycles → four edges, plus the initial write.
    assert 4 <= len(hardware.writes) <= 6


def test_slow_blink_holds_for_the_documented_duration(rig):
    controller, hardware, clock = rig
    controller.set_state(Lamp.WIFI, Service.SEARCHING)
    bit = Calibration().bit("wifi")

    for _ in range(int(1100 / TICK_MS)):
        controller.tick()
        clock.advance_ms(TICK_MS)

    history = hardware.bit_history(bit)
    assert history[0][1] == 1
    # First transition to dark should land at 550 ms, within one tick.
    assert history[1][0] - history[0][0] == pytest.approx(0.550, abs=TICK_MS / 1000)


def test_run_leaves_the_lamps_dark(rig):
    import asyncio

    controller, hardware, _ = rig
    controller.set_state(Lamp.TELEX, Service.CONNECTED)

    async def scenario():
        stop = asyncio.Event()
        task = asyncio.create_task(controller.run(stop))
        await asyncio.sleep(0.05)
        stop.set()
        await task

    asyncio.run(scenario())
    assert hardware.byte == 0
