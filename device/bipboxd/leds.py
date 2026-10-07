"""The four panel lamps: patterns, per-lamp state, and the driver loop.

One task owns the shift register (architecture.md §2.1), ticks at 25 ms, and
writes a byte only when it changes. Each lamp resolves to a pattern from its
service state, which an activity flash overrides while it lasts (Q31).
"""

from __future__ import annotations

import asyncio
import enum
import time

from .calibration import Calibration

# The controller tick. Every timing below is a multiple of it, so each phase
# runs at exactly its written duration — at 20 ms, 175 and 550 would quantise
# and no pattern would match its documentation (architecture.md §3.4).
TICK_MS = 25

# (on_ms, off_ms) phases, repeating.
PATTERNS: dict[str, list[tuple[int, int]]] = {
    "off": [(0, TICK_MS)],
    "on": [(TICK_MS, 0)],
    "slow": [(550, 550)],
    "fast": [(175, 175)],
    "heartbeat": [(200, 2800)],
    "ap": [(200, 200), (200, 200), (500, 200)],
}

# How long an activity flash lasts. It must be at least one full `fast` cycle,
# or the blink can be swallowed entirely: at 175/175 a 150 ms flash landing in
# the pattern's lit phase produces no visible change at all on a lamp that is
# already steady-on, which is exactly when activity matters most.
FLASH_HOLD_MS = 350


class Lamp(enum.StrEnum):
    TELEGRAPHY = "telegraphy"
    TELEX = "telex"
    VOIP = "voip"
    WIFI = "wifi"


class Service(enum.StrEnum):
    """What a subsystem is doing, independent of which lamp shows it."""

    DOWN = "down"
    SEARCHING = "searching"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    AP_MODE = "ap_mode"


# Per-lamp state → pattern. Written out per lamp rather than shared, because
# the telegraphy lamp is deliberately inverted: its steady-on means "someone
# else is pressing" and "all well" is dark (architecture.md §3.4).
_STATE_PATTERNS: dict[Lamp, dict[Service, str]] = {
    Lamp.WIFI: {
        Service.DOWN: "off",
        Service.SEARCHING: "slow",
        Service.CONNECTING: "fast",
        Service.CONNECTED: "on",
        Service.AP_MODE: "ap",
    },
    Lamp.TELEX: {
        Service.DOWN: "off",
        Service.SEARCHING: "slow",
        Service.CONNECTING: "slow",
        Service.CONNECTED: "on",
        Service.AP_MODE: "off",
    },
    Lamp.VOIP: {
        Service.DOWN: "off",
        Service.SEARCHING: "slow",
        Service.CONNECTING: "slow",
        Service.CONNECTED: "on",
        Service.AP_MODE: "off",
    },
    Lamp.TELEGRAPHY: {
        Service.DOWN: "heartbeat",
        Service.SEARCHING: "slow",
        Service.CONNECTING: "slow",
        Service.CONNECTED: "off",
        Service.AP_MODE: "heartbeat",
    },
}


def pattern_for(lamp: Lamp, state: Service) -> str:
    return _STATE_PATTERNS[lamp][state]


def pattern_level(name: str, elapsed_ms: float) -> int:
    """Whether a pattern is lit at a given point in its cycle."""
    phases = PATTERNS[name]
    period = sum(on + off for on, off in phases)
    if period <= 0:
        return 0
    position = elapsed_ms % period
    for on, off in phases:
        if position < on:
            return 1
        position -= on
        if position < off:
            return 0
        position -= off
    return 0


class LedController:
    """Drives the lamps from their states. Not itself async-aware until run()."""

    def __init__(self, hardware, calibration: Calibration, clock=time.monotonic):
        self._hw = hardware
        self._cal = calibration
        self._clock = clock
        self._started = clock()
        self._states: dict[Lamp, Service] = dict.fromkeys(Lamp, Service.DOWN)
        self._flash_until: dict[Lamp, float] = {}
        self._flash_start: dict[Lamp, float] = {}
        self._forced_on: set[Lamp] = set()
        self._last_byte: int | None = None

    # ── State in ─────────────────────────────────────────────────────────────

    def set_state(self, lamp: Lamp, state: Service) -> None:
        self._states[lamp] = state

    def state(self, lamp: Lamp) -> Service:
        return self._states[lamp]

    def flash(self, lamp: Lamp, hold_ms: int = FLASH_HOLD_MS) -> None:
        """Signal activity: overrides the state pattern while it lasts.

        The flash carries its own phase origin so it always *starts dark*. On a
        lamp that is already steady-on — telex connected and then receiving —
        a gap is the only thing the eye can register; starting lit would show
        nothing. Repeated calls extend the flash without restarting its phase,
        so sustained activity reads as a continuous fast blink.
        """
        now = self._clock()
        if self._flash_until.get(lamp, 0) <= now:
            self._flash_start[lamp] = now
        self._flash_until[lamp] = now + hold_ms / 1000

    def set_forced_on(self, lamp: Lamp, on: bool) -> None:
        """Hold a lamp steady regardless of state.

        This is how the telegraphy lamp shows someone else holding their
        button: it is an event, not a state, and it outranks both.
        """
        if on:
            self._forced_on.add(lamp)
        else:
            self._forced_on.discard(lamp)

    # ── Rendering ────────────────────────────────────────────────────────────

    def byte_at(self, now: float | None = None) -> int:
        now = self._clock() if now is None else now
        elapsed_ms = (now - self._started) * 1000
        byte = 0
        for lamp in Lamp:
            bit = self._cal.bit(lamp.value)
            if lamp in self._forced_on:
                lit = 1
            elif self._flash_until.get(lamp, 0) > now:
                # Offset by the lit phase so the flash opens with a gap.
                since = (now - self._flash_start.get(lamp, now)) * 1000
                lit = pattern_level("fast", since + PATTERNS["fast"][0][0])
            else:
                lit = pattern_level(pattern_for(lamp, self._states[lamp]), elapsed_ms)
            byte |= lit << bit
        return byte

    def tick(self, now: float | None = None) -> int:
        """Render and write. Writing is skipped when nothing changed.

        The de-duplication lives here rather than in the hardware so that the
        fake and the real implementation behave identically — an interface
        whose two sides differ is not worth having.
        """
        byte = self.byte_at(now)
        if byte != self._last_byte:
            self._last_byte = byte
            self._hw.write_leds(byte)
        return byte

    async def run(self, stop: asyncio.Event | None = None) -> None:
        interval = TICK_MS / 1000
        try:
            while stop is None or not stop.is_set():
                self.tick()
                await asyncio.sleep(interval)
        finally:
            self._last_byte = 0
            self._hw.write_leds(0)
