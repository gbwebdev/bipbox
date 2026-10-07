"""In-memory hardware, for development off the Pi and for tests.

It records the full sequence of bytes written, with timestamps, so a test can
assert that a blink pattern actually produced the right on/off durations rather
than merely that a function was called.
"""

from __future__ import annotations

import time


class FakeHardware:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self.byte = 0
        self.writes: list[tuple[float, int]] = []
        self.levels: dict[int, int] = {}
        self.closed = False

    # ── Hardware protocol ────────────────────────────────────────────────────

    def write_leds(self, byte: int) -> None:
        if self.closed:
            raise RuntimeError("write after close")
        self.byte = byte & 0xFF
        self.writes.append((self._clock(), self.byte))

    def read_button(self, pin: int) -> int:
        return self.levels.get(pin, 1)  # idle high, like a real pull-up

    def close(self) -> None:
        self.byte = 0
        self.closed = True

    # ── Test helpers ─────────────────────────────────────────────────────────

    def press(self, pin: int) -> None:
        self.levels[pin] = 0

    def release(self, pin: int) -> None:
        self.levels[pin] = 1

    def bit_history(self, bit: int) -> list[tuple[float, int]]:
        """Timeline of one bit, with repeats collapsed."""
        out: list[tuple[float, int]] = []
        for at, byte in self.writes:
            value = (byte >> bit) & 1
            if not out or out[-1][1] != value:
                out.append((at, value))
        return out

    def is_lit(self, bit: int) -> bool:
        return bool((self.byte >> bit) & 1)
