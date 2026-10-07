"""The hardware surface the daemon is allowed to use."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Hardware(Protocol):
    """Deliberately tiny: one byte out for the lamps, two bits in for buttons.

    Keeping it this narrow is what makes the fake implementation trustworthy —
    there is nowhere for behaviour to hide that only the real one has.
    """

    def write_leds(self, byte: int) -> None:
        """Latch one byte into the '595. Bit 0 is QA."""

    def read_button(self, pin: int) -> int:
        """Raw electrical level: 0 pressed (active-low), 1 released."""

    def close(self) -> None:
        """Release the bus and leave the lamps dark."""
