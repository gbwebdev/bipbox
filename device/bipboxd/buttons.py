"""Debounced button input for the telegraphy key and the PTT.

Both are active-low (architecture.md §3.1). The HAT debounces the telegraphy
key in hardware and the PTT variants differ in edge speed, so the debounce
window is per-button and comes from the calibration.

Acceptance is by *stable level*, not by a timeout after the first edge: an
earlier version of the bring-up tool suppressed events for 80 ms after any
edge, which silently swallowed the release of every press shorter than that.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass

PIN_TELEGRAPHY = 2
PIN_PTT = 23

POLL_MS = 2


@dataclass
class ButtonState:
    pin: int
    debounce_ms: int
    # Which electrical level means "pressed". The telegraphy key and the
    # dedicated-PTT wiring ground the pin, so they are active-low. The shared
    # mic/PTT wiring is the opposite: the transistor conducts while the mic
    # line carries its idle bias, holding the pin LOW, and pressing PTT
    # collapses that bias so the pin floats HIGH (architecture.md §3.3).
    active_low: bool = True
    level: int = 1
    raw_level: int = 1
    changed_at: float = 0.0
    pressed_at: float | None = None
    presses: int = 0

    @property
    def pressed_level(self) -> int:
        return 0 if self.active_low else 1

    @property
    def is_pressed(self) -> bool:
        return self.level == self.pressed_level


class ButtonWatcher:
    """Polls buttons and reports accepted press/release transitions.

    Polling rather than edge callbacks: it keeps the debounce logic explicit
    and testable, and at a 2 ms interval it costs nothing measurable even on an
    ARMv6 core.
    """

    def __init__(self, hardware, buttons: dict[str, ButtonState], clock=time.monotonic):
        self._hw = hardware
        self._clock = clock
        self.buttons = buttons
        self._on_press: dict[str, list[Callable[[], None]]] = {}
        self._on_release: dict[str, list[Callable[[float], None]]] = {}
        now = clock()
        for state in self.buttons.values():
            state.raw_level = state.level = self._hw.read_button(state.pin)
            state.changed_at = now

    @classmethod
    def from_calibration(cls, hardware, calibration, clock=time.monotonic) -> ButtonWatcher:
        return cls(
            hardware,
            {
                "telegraphy": ButtonState(
                    PIN_TELEGRAPHY, calibration.telegraphy_debounce_ms, active_low=True
                ),
                "ptt": ButtonState(
                    PIN_PTT, calibration.ptt_debounce_ms, active_low=calibration.ptt_active_low
                ),
            },
            clock=clock,
        )

    def on_press(self, name: str, callback: Callable[[], None]) -> None:
        self._on_press.setdefault(name, []).append(callback)

    def on_release(self, name: str, callback: Callable[[float], None]) -> None:
        """Callback receives how long the button was held, in milliseconds."""
        self._on_release.setdefault(name, []).append(callback)

    def poll(self, now: float | None = None) -> list[tuple[str, str, float]]:
        """Read every button once. Returns the accepted transitions."""
        now = self._clock() if now is None else now
        events: list[tuple[str, str, float]] = []

        for name, state in self.buttons.items():
            raw = self._hw.read_button(state.pin)
            if raw != state.raw_level:
                state.raw_level = raw
                state.changed_at = now

            settled = (now - state.changed_at) * 1000 >= state.debounce_ms
            if raw == state.level or not settled:
                continue

            state.level = raw
            if raw == state.pressed_level:
                state.presses += 1
                state.pressed_at = now
                events.append((name, "press", 0.0))
                for callback in self._on_press.get(name, []):
                    callback()
            else:
                held = (now - state.pressed_at) * 1000 if state.pressed_at is not None else 0.0
                state.pressed_at = None
                events.append((name, "release", held))
                for callback in self._on_release.get(name, []):
                    callback(held)

        return events

    def is_pressed(self, name: str) -> bool:
        return self.buttons[name].is_pressed

    async def run(self, stop: asyncio.Event | None = None) -> None:
        interval = POLL_MS / 1000
        while stop is None or not stop.is_set():
            self.poll()
            await asyncio.sleep(interval)
