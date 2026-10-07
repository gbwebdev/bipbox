"""SPI and GPIO on a Raspberry Pi.

The '595 is latched by SPI0's CE1: spidev holds CS low for the transfer and
releases it high afterwards, and that trailing rising edge is exactly what RCLK
wants (architecture.md §3.2). Nothing here is testable in CI, which is why it
contains no logic — only the bus calls.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

SPI_BUS = 0
SPI_DEVICE = 1  # CE1 → RCLK
SPI_HZ = 1_000_000


class RealHardware:
    def __init__(self, pins: tuple[int, ...] = ()):
        import spidev
        from gpiozero import DigitalInputDevice

        self._spi = spidev.SpiDev()
        self._spi.open(SPI_BUS, SPI_DEVICE)
        self._spi.max_speed_hz = SPI_HZ
        self._spi.mode = 0
        self.byte = 0

        # GPIO2 has a fixed 1.8k pull-up on the Pi board; GPIO23 relies on the
        # internal one. pull_up=True is correct for both.
        self._inputs: dict[int, DigitalInputDevice] = {}
        self._make_input = DigitalInputDevice
        for pin in pins:
            self._open_pin(pin)

        self.write_leds(0)

    def _open_pin(self, pin: int):
        device = self._make_input(pin, pull_up=True)
        self._inputs[pin] = device
        return device

    def write_leds(self, byte: int) -> None:
        byte &= 0xFF
        if byte == self.byte and self._spi is not None:
            return  # the '595 holds its outputs; re-latching gains nothing
        self.byte = byte
        self._spi.xfer2([byte])

    def read_button(self, pin: int) -> int:
        device = self._inputs.get(pin) or self._open_pin(pin)
        # gpiozero inverts for pull_up=True: .value is 1 when the pin is LOW.
        return 0 if device.value else 1

    def close(self) -> None:
        try:
            self._spi.xfer2([0])
            self._spi.close()
        except Exception:
            log.warning("SPI close failed", exc_info=True)
        for device in self._inputs.values():
            try:
                device.close()
            except Exception:
                log.warning("GPIO close failed", exc_info=True)
        self._inputs.clear()
