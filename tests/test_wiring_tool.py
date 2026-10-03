"""Tests for the hardware-independent parts of device/tools/wiring_test.py.

The SPI, GPIO, ALSA and ESC/POS paths can only be verified on the bench; what is
testable here is tone synthesis, level metering, state persistence and the blink
patterns, which is also where a silent regression would be hardest to notice.
"""

import importlib.util
import math
import wave
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[1] / "device" / "tools" / "wiring_test.py"


@pytest.fixture(scope="module")
def tool():
    spec = importlib.util.spec_from_file_location("wiring_test", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_tone_is_mono_16bit_at_requested_rate(tool, tmp_path):
    path = tmp_path / "tone.wav"
    tool.write_tone(path, freq=880, seconds=0.25, rate=8000)

    with wave.open(str(path), "rb") as w:
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getframerate() == 8000
        assert w.getnframes() == 2000


def test_tone_respects_requested_amplitude(tool, tmp_path):
    quiet, loud = tmp_path / "q.wav", tmp_path / "l.wav"
    tool.write_tone(quiet, seconds=0.3, volume=0.2)
    tool.write_tone(loud, seconds=0.3, volume=0.8)

    assert tool.peak_level(quiet) == pytest.approx(0.2, abs=0.02)
    assert tool.peak_level(loud) == pytest.approx(0.8, abs=0.02)


def test_tone_fades_in_to_avoid_a_click(tool, tmp_path):
    """A hard edge at t=0 clicks through a small speaker, so the ramp matters."""
    path = tmp_path / "tone.wav"
    rate = 8000
    tool.write_tone(path, seconds=0.5, rate=rate, volume=1.0)

    with wave.open(str(path), "rb") as w:
        head = w.readframes(1)
        w.setpos(int(rate * 0.25))
        middle = w.readframes(int(rate * 0.02))

    first = int.from_bytes(head, "little", signed=True)
    peak_middle = max(
        abs(int.from_bytes(middle[i : i + 2], "little", signed=True))
        for i in range(0, len(middle), 2)
    )
    assert abs(first) < 0.05 * peak_middle


def test_peak_level_of_silence_is_zero(tool, tmp_path):
    path = tmp_path / "silence.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00" * 800)

    assert tool.peak_level(path) == 0.0


def test_peak_level_of_a_missing_file_does_not_raise(tool, tmp_path):
    assert tool.peak_level(tmp_path / "nope.wav") == 0.0


def test_state_round_trips_and_merges(tool, tmp_path, monkeypatch):
    monkeypatch.setattr(tool, "STATE_FILE", tmp_path / "state.json")

    tool.save_state(volume_max_pct=45)
    tool.save_state(led_bits={"telex": 3})

    state = tool.load_state()
    assert state == {"volume_max_pct": 45, "led_bits": {"telex": 3}}


def test_led_bits_fall_back_to_the_assumed_mapping(tool, tmp_path, monkeypatch):
    monkeypatch.setattr(tool, "STATE_FILE", tmp_path / "absent.json")
    assert tool.load_led_bits() == tool.DEFAULT_LED_BITS


def test_led_bits_prefer_a_discovered_mapping(tool, tmp_path, monkeypatch):
    monkeypatch.setattr(tool, "STATE_FILE", tmp_path / "state.json")
    tool.save_state(led_bits={"telegraphy": 7, "wifi": 0})

    assert tool.load_led_bits() == {"telegraphy": 7, "wifi": 0}


def test_volume_defaults_are_conservative(tool):
    """A freshly flashed box must never arrive loud (architecture.md §6.3.1)."""
    assert tool.DEFAULT_VOLUME_PCT == 40
    assert tool.VOLUME_SWEEP[0] <= 10
    assert max(tool.VOLUME_SWEEP) > tool.VOLUME_WARN_PCT
    assert sorted(tool.VOLUME_SWEEP) == tool.VOLUME_SWEEP


def test_blink_patterns_match_the_specified_timings(tool):
    assert tool.PATTERNS["slow"] == [(500, 500)]
    assert tool.PATTERNS["fast"] == [(100, 100)]
    assert tool.PATTERNS["heartbeat"] == [(80, 1920)]
    # ". . _" repeating, with no trailing pause (Q30).
    assert tool.PATTERNS["ap"] == [(150, 150), (150, 150), (450, 150)]


def test_heartbeat_period_is_two_seconds(tool):
    on, off = tool.PATTERNS["heartbeat"][0]
    assert on + off == 2000


def test_tone_frequency_is_recoverable(tool, tmp_path):
    """Guards against an off-by-2pi or rate mix-up in the synthesis."""
    path = tmp_path / "tone.wav"
    rate, freq = 8000, 1000
    tool.write_tone(path, freq=freq, seconds=0.5, rate=rate, volume=1.0)

    with wave.open(str(path), "rb") as w:
        w.setpos(int(rate * 0.1))
        raw = w.readframes(int(rate * 0.3))

    samples = [int.from_bytes(raw[i : i + 2], "little", signed=True) for i in range(0, len(raw), 2)]
    crossings = sum(
        1 for a, b in zip(samples, samples[1:], strict=False) if a <= 0 < b or a >= 0 > b
    )
    measured = crossings / 2 / (len(samples) / rate)
    assert measured == pytest.approx(freq, rel=0.05)


def test_mixer_candidates_are_ordered_most_specific_first(tool):
    assert tool.MIXER_CANDIDATES[0] == "PCM"
    assert "Master" in tool.MIXER_CANDIDATES


def test_pin_assignments_match_the_architecture(tool):
    assert tool.PIN_TELEGRAPHY == 2
    assert tool.PIN_PTT == 23


def test_default_led_bits_cover_all_four_lamps(tool):
    assert set(tool.DEFAULT_LED_BITS) == {"telegraphy", "telex", "voip", "wifi"}
    assert len(set(tool.DEFAULT_LED_BITS.values())) == 4
    assert all(0 <= bit <= 7 for bit in tool.DEFAULT_LED_BITS.values())


def test_tone_length_is_exact_for_odd_durations(tool, tmp_path):
    path = tmp_path / "tone.wav"
    tool.write_tone(path, seconds=0.137, rate=8000)
    with wave.open(str(path), "rb") as w:
        assert w.getnframes() == int(8000 * 0.137)


def test_sine_stays_within_int16(tool, tmp_path):
    """volume=1.0 must not wrap around to negative full scale."""
    path = tmp_path / "tone.wav"
    tool.write_tone(path, seconds=0.2, volume=1.0)
    with wave.open(str(path), "rb") as w:
        raw = w.readframes(w.getnframes())
    assert all(
        -32768 <= int.from_bytes(raw[i : i + 2], "little", signed=True) <= 32767
        for i in range(0, len(raw), 2)
    )
    assert math.isclose(tool.peak_level(path), 1.0, abs_tol=0.01)
