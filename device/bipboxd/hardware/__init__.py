"""Hardware access, behind an interface thin enough to fake completely.

Everything that touches SPI or GPIO goes through `Hardware`. The real
implementation needs a Pi; the fake one needs nothing and records what it was
asked to do, which is what lets the LED state machine and button handling be
developed and tested anywhere (architecture.md §12, phase 1).
"""

from __future__ import annotations

from .fake import FakeHardware
from .interface import Hardware

__all__ = ["FakeHardware", "Hardware", "open_hardware"]


def open_hardware(fake: bool = False) -> Hardware:
    """Return real hardware, falling back to the fake one when unavailable.

    The fallback is deliberate and loud: running on a laptop is a normal part
    of development, and a confusing ImportError at startup is not.
    """
    if fake:
        return FakeHardware()

    from .real import RealHardware

    return RealHardware()
