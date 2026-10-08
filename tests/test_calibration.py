"""Calibration loading and validation.

The validation matters more than it looks: a calibration that is merely *wrong*
produces lamps that light in the wrong places, which reads as a hardware fault
and costs an hour on the bench. Rejecting it loudly at startup is cheaper.
"""

import dataclasses
import json

import pytest
from bipboxd.calibration import (
    DEFAULT_LED_BITS,
    Calibration,
    CalibrationError,
)

BOX_A = {
    "led_bits": {"telex": 4, "telegraphy": 2, "voip": 3, "wifi": 5},
    "capture_pct": 60,
    "capture_control": "Mic",
    "playback_gain": 4,
    "volume_max_pct": 90,
    "mixer_control": "Speaker",
}


def test_box_a_calibration_loads(tmp_path):
    path = tmp_path / "calibration.json"
    path.write_text(json.dumps(BOX_A))

    cal = Calibration.load(path)

    assert cal.led_bits == {"telex": 4, "telegraphy": 2, "voip": 3, "wifi": 5}
    assert cal.mixer_control == "Speaker"
    assert cal.capture_control == "Mic"
    assert cal.volume_max_pct == 90
    assert cal.capture_pct == 60
    assert cal.playback_gain == 4


def test_the_wiring_tool_output_is_accepted_verbatim(tmp_path):
    """The bring-up file IS the calibration — no hand-editing step."""
    path = tmp_path / "wiring_test.json"
    path.write_text(json.dumps({**BOX_A, "some_bringup_note": "ignored"}))

    cal = Calibration.load(path)

    assert cal.volume_max_pct == 90


def test_missing_file_falls_back_to_defaults(tmp_path):
    cal = Calibration.load(tmp_path / "absent.json")
    assert cal.led_bits == DEFAULT_LED_BITS


def test_defaults_describe_box_a_greens_reversed():
    """QD is VoIP and QE is telex — measured, not assumed (architecture.md §3.2)."""
    cal = Calibration()
    assert cal.bit("voip") == 3
    assert cal.bit("telex") == 4


def test_a_lamp_with_no_bit_is_rejected():
    with pytest.raises(CalibrationError, match="telex"):
        Calibration(led_bits={"telegraphy": 2, "voip": 3, "wifi": 5})


def test_two_lamps_on_one_bit_are_rejected():
    """Otherwise one lamp is simply unaddressable, which looks like a dead LED."""
    with pytest.raises(CalibrationError, match="share a bit"):
        Calibration(led_bits={"telegraphy": 2, "voip": 3, "telex": 3, "wifi": 5})


def test_a_bit_outside_the_register_is_rejected():
    with pytest.raises(CalibrationError, match="outside"):
        Calibration(led_bits={**DEFAULT_LED_BITS, "wifi": 8})


def test_an_unknown_lamp_is_rejected():
    with pytest.raises(CalibrationError, match="unknown lamp"):
        Calibration(led_bits={**DEFAULT_LED_BITS, "porch": 6})


@pytest.mark.parametrize("field", ["volume_max_pct", "capture_pct"])
@pytest.mark.parametrize("value", [-1, 101])
def test_percentages_must_be_percentages(field, value):
    with pytest.raises(CalibrationError, match="percentage"):
        Calibration(**{field: value})


def test_playback_gain_below_unity_is_rejected():
    """Attenuation here would fight the mixer rather than complement it."""
    with pytest.raises(CalibrationError, match="attenuate"):
        Calibration(playback_gain=0.5)


def test_unknown_ptt_wiring_is_rejected():
    with pytest.raises(CalibrationError, match="dedicated or shared"):
        Calibration(ptt_wiring="telepathy")


def test_malformed_json_is_rejected_with_the_path(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{not json")

    with pytest.raises(CalibrationError, match="not valid JSON"):
        Calibration.load(path)


def test_a_json_list_is_rejected(tmp_path):
    path = tmp_path / "list.json"
    path.write_text("[1, 2, 3]")

    with pytest.raises(CalibrationError, match="JSON object"):
        Calibration.load(path)


def test_mask_covers_every_mapped_bit():
    cal = Calibration()
    assert cal.mask == (1 << 2) | (1 << 3) | (1 << 4) | (1 << 5)


def test_round_trip_through_a_dict():
    cal = Calibration.from_dict(BOX_A)
    assert Calibration.from_dict(cal.to_dict()) == cal


def test_calibration_is_immutable():
    """It is read once at startup; later mutation would desync the lamps."""
    cal = Calibration()
    with pytest.raises(dataclasses.FrozenInstanceError):
        cal.volume_max_pct = 10  # type: ignore[misc]
