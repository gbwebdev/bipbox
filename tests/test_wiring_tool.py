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
    assert tool.DEFAULT_VOLUME_PCT == 70  # measured ceiling, 2026-10-07
    assert tool.DEFAULT_VOLUME_PCT <= tool.VOLUME_WARN_PCT
    assert tool.VOLUME_SWEEP[0] <= 10
    assert max(tool.VOLUME_SWEEP) > tool.VOLUME_WARN_PCT
    assert sorted(tool.VOLUME_SWEEP) == tool.VOLUME_SWEEP


def test_telex_is_not_swallowed_by_telegraphy(tool):
    """Regression: 'telex' and 'telegraphy' collide on a 4-character prefix.

    The original matcher accepted `answer.startswith(name[:4])`, so typing
    "telex" matched "telegraphy", filed telex's bit under telegraphy, and left
    telex permanently unmapped — with no error shown.
    """
    assert tool.resolve_led_name("telex") == "telex"
    assert tool.resolve_led_name("telegraphy") == "telegraphy"


def test_resolve_led_name_accepts_unambiguous_prefixes(tool):
    assert tool.resolve_led_name("v") == "voip"
    assert tool.resolve_led_name("w") == "wifi"
    assert tool.resolve_led_name("teleg") == "telegraphy"


def test_resolve_led_name_rejects_ambiguity_and_nonsense(tool):
    assert tool.resolve_led_name("tele") is None  # telegraphy vs telex
    assert tool.resolve_led_name("t") is None
    assert tool.resolve_led_name("banana") is None


def test_resolve_led_name_is_case_and_space_insensitive(tool):
    assert tool.resolve_led_name("  TeLeX  ") == "telex"


def test_capture_window_is_sane(tool):
    assert 0 < tool.CAPTURE_TARGET_MIN < tool.CAPTURE_TARGET_MAX < 1.0
    assert sorted(tool.CAPTURE_SWEEP) == tool.CAPTURE_SWEEP
    assert tool.DEFAULT_CAPTURE_PCT in tool.CAPTURE_SWEEP


def _write(path, samples, rate=8000):
    import array

    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(array.array("h", samples).tobytes())


def test_sine_crest_factor_is_about_three_db(tool, tmp_path):
    """The reference point for the whole loudness argument (architecture.md §6.3.3)."""
    path = tmp_path / "tone.wav"
    tool.write_tone(path, seconds=0.5, volume=0.9)
    assert tool.crest_db(path) == pytest.approx(3.0, abs=0.5)


def test_bursty_signal_has_a_much_higher_crest_than_a_sine(tool, tmp_path):
    """Why a tone is loud and speech is not, at the same peak level."""
    rate = 8000
    bursty = [
        int(30000 * (1.0 if (i // 400) % 5 == 0 else 0.03) * math.sin(2 * math.pi * 300 * i / rate))
        for i in range(rate)
    ]
    path = tmp_path / "bursty.wav"
    _write(path, bursty, rate)

    tone = tmp_path / "tone.wav"
    tool.write_tone(tone, seconds=0.5, volume=0.9)

    assert tool.crest_db(path) > tool.crest_db(tone) + 5
    assert tool.peak_level(path) == pytest.approx(tool.peak_level(tone), abs=0.05)
    assert tool.rms_level(path) < tool.rms_level(tone)


def test_apply_gain_raises_rms_and_reports_clipping(tool, tmp_path):
    rate = 8000
    src = tmp_path / "src.wav"
    _write(src, [int(8000 * math.sin(2 * math.pi * 300 * i / rate)) for i in range(rate)], rate)
    dst = tmp_path / "dst.wav"

    before = tool.rms_level(src)
    clipped = tool.apply_gain(src, dst, 2)

    assert clipped == 0.0  # 8000 x2 still fits in int16
    assert tool.rms_level(dst) == pytest.approx(2 * before, rel=0.02)


def test_apply_gain_hard_clips_instead_of_wrapping(tool, tmp_path):
    """Integer overflow would wrap to the opposite rail and sound catastrophic."""
    rate = 8000
    src = tmp_path / "src.wav"
    _write(src, [int(30000 * math.sin(2 * math.pi * 300 * i / rate)) for i in range(rate)], rate)
    dst = tmp_path / "dst.wav"

    clipped = tool.apply_gain(src, dst, 8)

    assert clipped > 0.5
    samples, _ = tool.read_samples(dst)
    assert max(samples) == 32767
    assert min(samples) >= -32768
    assert tool.peak_level(dst) == pytest.approx(1.0, abs=0.001)


def test_gain_of_one_is_a_no_op(tool, tmp_path):
    rate = 8000
    src = tmp_path / "src.wav"
    _write(src, [int(10000 * math.sin(2 * math.pi * 300 * i / rate)) for i in range(rate)], rate)
    dst = tmp_path / "dst.wav"

    assert tool.apply_gain(src, dst, 1) == 0.0
    assert tool.read_samples(src)[0] == tool.read_samples(dst)[0]


def test_gain_sweep_starts_at_unity(tool):
    assert tool.GAIN_SWEEP[0] == 1
    assert sorted(tool.GAIN_SWEEP) == tool.GAIN_SWEEP


def test_rms_and_crest_of_a_missing_file_do_not_raise(tool, tmp_path):
    assert tool.rms_level(tmp_path / "nope.wav") == 0.0
    assert tool.crest_db(tmp_path / "nope.wav") == 0.0


def test_level_bar_verdicts(tool):
    assert "SILENT" in tool.level_bar(0.0)
    assert "weak" in tool.level_bar(0.10)
    assert "good" in tool.level_bar(0.50)
    assert "hot" in tool.level_bar(0.92)
    assert "CLIPPING" in tool.level_bar(1.0)


def test_blink_patterns_match_the_specified_timings(tool):
    """Timings as judged on real LEDs: the originals read too brief and too quick."""
    assert tool.PATTERNS["slow"] == [(550, 550)]
    assert tool.PATTERNS["fast"] == [(175, 175)]
    assert tool.PATTERNS["heartbeat"] == [(200, 2800)]
    # ". . _" repeating, with no trailing pause (Q30).
    assert tool.PATTERNS["ap"] == [(200, 200), (200, 200), (500, 200)]


def test_heartbeat_is_a_brief_blink_on_a_three_second_cycle(tool):
    on, off = tool.PATTERNS["heartbeat"][0]
    assert on + off == 3000
    # The gap must dominate, or it stops reading as a heartbeat and becomes
    # just another slow blink.
    assert off > 10 * on


def test_fast_stays_distinguishable_from_slow(tool):
    """They appear on the same lamp at different times, so the ratio must hold.

    Fast has been lengthened twice; this guards the point where another
    adjustment would make the two patterns hard to tell apart.
    """
    fast_period = sum(tool.PATTERNS["fast"][0])
    slow_period = sum(tool.PATTERNS["slow"][0])
    assert slow_period >= 2.5 * fast_period


def test_every_timing_is_a_multiple_of_the_led_tick(tool):
    """The LED controller ticks at 25 ms (architecture.md §3.4).

    Timings that are not multiples of the tick get quantised, so a pattern
    would not run at the duration written here.
    """
    for name, phases in tool.PATTERNS.items():
        for on, off in phases:
            assert on % 25 == 0, f"{name}: on={on}"
            assert off % 25 == 0, f"{name}: off={off}"


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
