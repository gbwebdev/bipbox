"""Run the daemon skeleton.

    python3 -m bipboxd --fake-hardware         # anywhere
    sudo python3 -m bipboxd                    # on the Pi

Phase 1 wires up the lamps and the buttons only: there is no server connection
yet, so `--demo` is provided to walk the lamp states so the panel can be seen
working end to end.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import sys
from pathlib import Path

from .buttons import ButtonWatcher
from .calibration import Calibration, CalibrationError
from .hardware import open_hardware
from .leds import Lamp, LedController, Service

log = logging.getLogger("bipboxd")

DEMO_SEQUENCE = [
    (Service.DOWN, 3),
    (Service.SEARCHING, 4),
    (Service.CONNECTING, 4),
    (Service.CONNECTED, 4),
    (Service.AP_MODE, 5),
]


async def demo(leds: LedController, stop: asyncio.Event) -> None:
    """Walk every lamp through every state, so the panel can be eyeballed."""
    while not stop.is_set():
        for state, seconds in DEMO_SEQUENCE:
            if stop.is_set():
                return
            log.info("state → %s", state.value)
            for lamp in Lamp:
                leds.set_state(lamp, state)
            await asyncio.sleep(seconds)

        log.info("activity flashes")
        for lamp in (Lamp.TELEX, Lamp.VOIP):
            leds.set_state(lamp, Service.CONNECTED)
        for _ in range(6):
            leds.flash(Lamp.TELEX)
            await asyncio.sleep(0.3)

        log.info("telegraphy incoming")
        leds.set_forced_on(Lamp.TELEGRAPHY, True)
        await asyncio.sleep(1.5)
        leds.set_forced_on(Lamp.TELEGRAPHY, False)


def wire_buttons(buttons: ButtonWatcher, leds: LedController) -> None:
    """Local feedback only — the server half arrives in phase 3."""

    def telegraphy_pressed() -> None:
        log.info("telegraphy pressed")

    def telegraphy_released(held_ms: float) -> None:
        log.info("telegraphy released after %.0f ms", held_ms)

    def ptt_pressed() -> None:
        log.info("PTT pressed")
        leds.flash(Lamp.VOIP, hold_ms=400)

    def ptt_released(held_ms: float) -> None:
        log.info("PTT released after %.0f ms", held_ms)

    buttons.on_press("telegraphy", telegraphy_pressed)
    buttons.on_release("telegraphy", telegraphy_released)
    buttons.on_press("ptt", ptt_pressed)
    buttons.on_release("ptt", ptt_released)


async def main_async(args: argparse.Namespace) -> int:
    try:
        calibration = Calibration.load(Path(args.calibration) if args.calibration else None)
    except CalibrationError as e:
        log.error("calibration rejected: %s", e)
        return 2

    log.info("calibration: %s", calibration.led_bits)

    try:
        hardware = open_hardware(fake=args.fake_hardware)
    except Exception as e:
        log.error("no hardware: %s", e)
        log.error("run with --fake-hardware to work without a Pi")
        return 3

    leds = LedController(hardware, calibration)
    buttons = ButtonWatcher.from_calibration(hardware, calibration)
    wire_buttons(buttons, leds)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    with contextlib.suppress(NotImplementedError):
        import signal

        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)

    tasks = [
        asyncio.create_task(leds.run(stop), name="leds"),
        asyncio.create_task(buttons.run(stop), name="buttons"),
    ]
    if args.demo:
        tasks.append(asyncio.create_task(demo(leds, stop), name="demo"))

    log.info("running (Ctrl-C to stop)")
    try:
        await stop.wait()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        hardware.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bipboxd", description=__doc__.splitlines()[0])
    parser.add_argument("--fake-hardware", action="store_true", help="run without SPI or GPIO")
    parser.add_argument("--calibration", help="path to calibration.json")
    parser.add_argument("--demo", action="store_true", help="walk the lamp states")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
    )
    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
