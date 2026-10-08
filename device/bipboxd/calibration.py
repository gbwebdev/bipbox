"""Per-box hardware calibration.

These values describe one physical box and its wiring harness: which shift
register bit reaches which lamp, what the card's mixer controls are called, and
the gain settings found by ear. None of it is derivable from the schematic
(architecture.md §3.2, §3.6), and the box needs it before it can talk to
anything — so it lives on the device, not on the server.

`device/tools/wiring_test.py` produces exactly this shape, so its output file
can be installed directly as the calibration.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PATH = Path("/etc/bipbox/calibration.json")

LED_NAMES = ("telegraphy", "telex", "voip", "wifi")

# Measured on box A. Ships as a starting point, but the two greens depend on how
# the harness was crimped rather than on the board, so every box is walked.
DEFAULT_LED_BITS = {"telegraphy": 2, "voip": 3, "telex": 4, "wifi": 5}


class CalibrationError(ValueError):
    """Raised for a calibration that would silently misbehave if accepted."""


@dataclass(frozen=True)
class Calibration:
    led_bits: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_LED_BITS))
    mixer_control: str | None = None
    capture_control: str | None = None
    volume_max_pct: int = 70
    capture_pct: int = 60
    playback_gain: float = 1.0
    ptt_wiring: str = "dedicated"
    telegraphy_debounce_ms: int = 20
    ptt_debounce_ms: int = 20

    def __post_init__(self) -> None:
        missing = set(LED_NAMES) - set(self.led_bits)
        if missing:
            raise CalibrationError(f"no bit mapped for: {', '.join(sorted(missing))}")

        unknown = set(self.led_bits) - set(LED_NAMES)
        if unknown:
            raise CalibrationError(f"unknown lamp(s): {', '.join(sorted(unknown))}")

        for name, bit in self.led_bits.items():
            if not 0 <= bit <= 7:
                raise CalibrationError(f"{name}: bit {bit} is outside 0-7")

        # Two lamps on one bit means one of them can never be addressed, which
        # is the kind of fault that looks like a hardware problem for an hour.
        if len(set(self.led_bits.values())) != len(self.led_bits):
            raise CalibrationError(f"two lamps share a bit: {self.led_bits}")

        for field_name in ("volume_max_pct", "capture_pct"):
            value = getattr(self, field_name)
            if not 0 <= value <= 100:
                raise CalibrationError(f"{field_name}={value} is not a percentage")

        if self.playback_gain < 1:
            raise CalibrationError(f"playback_gain={self.playback_gain} would attenuate")

        if self.ptt_wiring not in ("dedicated", "shared"):
            raise CalibrationError(f"ptt_wiring={self.ptt_wiring!r} is not dedicated or shared")

    @property
    def ptt_active_low(self) -> bool:
        """Whether PTT grounds the GPIO when pressed. It depends on the wiring.

        `dedicated`: a switch on its own TRRS ring pulls the pin to ground, so
        pressed is LOW — the same as the telegraphy key.

        `shared`: the mic and PTT share one line. A transistor conducts while
        that line carries its idle bias, holding the pin LOW; pressing PTT
        collapses the bias, the transistor turns off, and the internal pull-up
        takes the pin HIGH. So pressed is HIGH — inverted (architecture.md §3.3).
        """
        return self.ptt_wiring == "dedicated"

    @property
    def mask(self) -> int:
        """Every mapped bit, for an all-on test."""
        return sum(1 << bit for bit in self.led_bits.values())

    def bit(self, lamp: str) -> int:
        try:
            return self.led_bits[lamp]
        except KeyError:
            raise CalibrationError(f"unknown lamp {lamp!r}") from None

    @classmethod
    def from_dict(cls, data: dict) -> Calibration:
        known = set(cls.__dataclass_fields__)
        # Unknown keys are ignored rather than rejected: wiring_test.json also
        # carries bring-up notes that are none of the daemon's business.
        return cls(**{k: v for k, v in data.items() if k in known})

    @classmethod
    def load(cls, path: Path | None = None) -> Calibration:
        path = path or DEFAULT_PATH
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError as e:
            raise CalibrationError(f"{path} is not valid JSON: {e}") from e
        if not isinstance(data, dict):
            raise CalibrationError(f"{path} must contain a JSON object")
        return cls.from_dict(data)

    def to_dict(self) -> dict:
        return {
            "led_bits": dict(self.led_bits),
            "mixer_control": self.mixer_control,
            "capture_control": self.capture_control,
            "volume_max_pct": self.volume_max_pct,
            "capture_pct": self.capture_pct,
            "playback_gain": self.playback_gain,
            "ptt_wiring": self.ptt_wiring,
            "telegraphy_debounce_ms": self.telegraphy_debounce_ms,
            "ptt_debounce_ms": self.ptt_debounce_ms,
        }
