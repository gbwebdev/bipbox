"""Button debouncing.

The regression that motivates most of this: the bring-up tool once suppressed
events for 80 ms after any edge, which silently swallowed the release of every
press shorter than that. A short tap must produce both a press and a release.
"""

import pytest
from bipboxd.buttons import PIN_PTT, PIN_TELEGRAPHY, ButtonState, ButtonWatcher
from bipboxd.calibration import Calibration
from bipboxd.hardware import FakeHardware


class Clock:
    def __init__(self):
        self.now = 500.0

    def __call__(self):
        return self.now

    def advance_ms(self, ms):
        self.now += ms / 1000


@pytest.fixture
def rig():
    clock = Clock()
    hardware = FakeHardware(clock=clock)
    watcher = ButtonWatcher(
        hardware,
        {"telegraphy": ButtonState(PIN_TELEGRAPHY, 20), "ptt": ButtonState(PIN_PTT, 20)},
        clock=clock,
    )
    return watcher, hardware, clock


def settle(watcher, clock, ms, step=2):
    """Poll across a span of time, as the real loop does."""
    events = []
    for _ in range(max(1, int(ms / step))):
        events += watcher.poll()
        clock.advance_ms(step)
    return events


def test_buttons_start_released(rig):
    watcher, _, _ = rig
    assert not watcher.is_pressed("telegraphy")
    assert not watcher.is_pressed("ptt")


def test_a_held_press_is_reported_once(rig):
    watcher, hardware, clock = rig
    hardware.press(PIN_TELEGRAPHY)

    events = settle(watcher, clock, 200)

    assert [(n, k) for n, k, _ in events] == [("telegraphy", "press")]
    assert watcher.is_pressed("telegraphy")


def test_a_short_tap_reports_both_press_and_release(rig):
    """The 80 ms regression: a 40 ms tap must not lose its release."""
    watcher, hardware, clock = rig

    hardware.press(PIN_TELEGRAPHY)
    settle(watcher, clock, 40)
    hardware.release(PIN_TELEGRAPHY)
    events = settle(watcher, clock, 100)

    kinds = [k for _, k, _ in events]
    assert kinds == ["release"]
    assert not watcher.is_pressed("telegraphy")
    assert watcher.buttons["telegraphy"].presses == 1


def test_release_reports_how_long_the_button_was_held(rig):
    watcher, hardware, clock = rig

    hardware.press(PIN_TELEGRAPHY)
    settle(watcher, clock, 30)
    hardware.release(PIN_TELEGRAPHY)
    events = settle(watcher, clock, 60)

    held = next(ms for _, kind, ms in events if kind == "release")
    # 30 ms of press plus the debounce windows on each edge.
    assert 30 <= held <= 90


def test_chatter_shorter_than_the_debounce_is_ignored(rig):
    watcher, hardware, clock = rig

    # Bounce for 10 ms with a 20 ms debounce window.
    for _ in range(5):
        hardware.press(PIN_TELEGRAPHY)
        settle(watcher, clock, 2)
        hardware.release(PIN_TELEGRAPHY)
        settle(watcher, clock, 2)

    events = settle(watcher, clock, 100)

    assert events == []
    assert watcher.buttons["telegraphy"].presses == 0


def test_bounce_then_a_settled_press_reports_exactly_one_press(rig):
    watcher, hardware, clock = rig

    for _ in range(4):
        hardware.press(PIN_TELEGRAPHY)
        settle(watcher, clock, 2)
        hardware.release(PIN_TELEGRAPHY)
        settle(watcher, clock, 2)
    hardware.press(PIN_TELEGRAPHY)
    events = settle(watcher, clock, 200)

    assert [k for _, k, _ in events] == ["press"]


def test_the_two_buttons_are_independent(rig):
    watcher, hardware, clock = rig

    hardware.press(PIN_PTT)
    events = settle(watcher, clock, 100)

    assert [(n, k) for n, k, _ in events] == [("ptt", "press")]
    assert watcher.is_pressed("ptt")
    assert not watcher.is_pressed("telegraphy")


def test_both_can_be_pressed_at_once(rig):
    watcher, hardware, clock = rig

    hardware.press(PIN_TELEGRAPHY)
    hardware.press(PIN_PTT)
    events = settle(watcher, clock, 100)

    assert {n for n, k, _ in events if k == "press"} == {"telegraphy", "ptt"}


def test_callbacks_fire_with_the_hold_time(rig):
    watcher, hardware, clock = rig
    pressed, released = [], []
    watcher.on_press("ptt", lambda: pressed.append(True))
    watcher.on_release("ptt", released.append)

    hardware.press(PIN_PTT)
    settle(watcher, clock, 50)
    hardware.release(PIN_PTT)
    settle(watcher, clock, 60)

    assert pressed == [True]
    assert len(released) == 1
    assert released[0] >= 50


def test_per_button_debounce_comes_from_the_calibration():
    """The shared-mic PTT variant has slower edges, so it gets a wider window."""
    clock = Clock()
    hardware = FakeHardware(clock=clock)
    cal = Calibration(ptt_wiring="shared", ptt_debounce_ms=50, telegraphy_debounce_ms=20)

    watcher = ButtonWatcher.from_calibration(hardware, cal, clock=clock)

    assert watcher.buttons["ptt"].debounce_ms == 50
    assert watcher.buttons["telegraphy"].debounce_ms == 20


def test_dedicated_ptt_wiring_is_active_low():
    assert Calibration(ptt_wiring="dedicated").ptt_active_low is True


def test_shared_ptt_wiring_is_active_high():
    """The mic line's idle bias holds the pin low; pressing PTT releases it."""
    assert Calibration(ptt_wiring="shared").ptt_active_low is False


def test_shared_wiring_detects_a_press_as_the_pin_going_high():
    clock = Clock()
    hardware = FakeHardware(clock=clock)
    cal = Calibration(ptt_wiring="shared", ptt_debounce_ms=20)
    watcher = ButtonWatcher.from_calibration(hardware, cal, clock=clock)

    # Idle for this variant is the pin held LOW by the conducting transistor.
    hardware.press(PIN_PTT)
    settle(watcher, clock, 100)
    assert not watcher.is_pressed("ptt")

    # Pressing PTT collapses the bias and the pin floats high.
    hardware.release(PIN_PTT)
    events = settle(watcher, clock, 100)

    assert [(n, k) for n, k, _ in events] == [("ptt", "press")]
    assert watcher.is_pressed("ptt")


def test_telegraphy_stays_active_low_whatever_the_ptt_variant():
    """Only the PTT polarity depends on the harness; the key never does."""
    clock = Clock()
    hardware = FakeHardware(clock=clock)
    watcher = ButtonWatcher.from_calibration(
        hardware, Calibration(ptt_wiring="shared"), clock=clock
    )

    assert watcher.buttons["telegraphy"].active_low is True
    assert watcher.buttons["ptt"].active_low is False

    hardware.press(PIN_TELEGRAPHY)
    events = settle(watcher, clock, 100)
    assert [(n, k) for n, k, _ in events] == [("telegraphy", "press")]


def test_a_wider_debounce_rejects_a_press_a_narrower_one_accepts():
    clock = Clock()
    hardware = FakeHardware(clock=clock)
    watcher = ButtonWatcher(
        hardware,
        {"slow": ButtonState(PIN_PTT, 50), "quick": ButtonState(PIN_TELEGRAPHY, 5)},
        clock=clock,
    )

    hardware.press(PIN_PTT)
    hardware.press(PIN_TELEGRAPHY)
    events = settle(watcher, clock, 20)

    assert [n for n, k, _ in events if k == "press"] == ["quick"]
