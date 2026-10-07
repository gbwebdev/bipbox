#!/usr/bin/env python3
"""
Bipbox HAT wiring test — standalone bring-up and field diagnostic tool.

Runs on a bare Raspberry Pi OS Lite install with no bipbox code present:
    sudo apt install python3-spidev python3-gpiozero
    sudo python3 wiring_test.py

Deliberately avoids python-escpos, pyusb and numpy so it stays installable on a
Pi Zero W in one apt line. Printing writes raw ESC/POS to the device node;
audio shells out to aplay/arecord.

Requires in /boot/firmware/config.txt:  dtparam=spi=on

Usage:
    sudo python3 wiring_test.py                interactive menu
    sudo python3 wiring_test.py --selftest     run everything, print a summary
    sudo python3 wiring_test.py --leds         LED tests only
    sudo python3 wiring_test.py --buttons      button monitor only
    sudo python3 wiring_test.py --buttons --pin 17 --pin 27
                                               monitor arbitrary GPIOs
    sudo python3 wiring_test.py --audio        audio test only
    sudo python3 wiring_test.py --volume-sweep find the safe speaker ceiling
    sudo python3 wiring_test.py --mic-sweep    find a usable capture gain
    sudo python3 wiring_test.py --printer      printer test only
"""

import argparse
import array
import glob
import json
import math
import os
import re
import subprocess
import sys
import time
import wave
from pathlib import Path

STATE_FILE = Path(__file__).with_name("wiring_test.json")

# Assumed 74HC(T)595 bit → LED mapping, shifting MSB-first so the first bit sent
# lands on QH. Overridden by whatever the bit-walk discovers.
DEFAULT_LED_BITS = {
    "telegraphy": 2,  # QC, orange, 180R
    "telex": 3,  # QD, green, 300R
    "voip": 4,  # QE, green, 300R
    "wifi": 5,  # QF, blue, 180R
}

PIN_TELEGRAPHY = 2
PIN_PTT = 23

# Speaker protection. The shoulder speakermic holds a small, cheap driver behind
# a 22R series resistor; sustained level is what kills it, so every test starts
# conservative and never jumps to full scale. 70% is the measured ceiling on
# the first box: 80% buzzes faintly (architecture.md 6.3.1).
DEFAULT_VOLUME_PCT = 70
VOLUME_WARN_PCT = 75  # above the measured-clean point, so 80+ asks first
VOLUME_SWEEP = [10, 20, 30, 40, 50, 60, 70, 80, 90]
MIXER_CANDIDATES = ("PCM", "Speaker", "Headphone", "Master", "Playback")

# Capture: the speakermic is a low-output dynamic element into a cheap USB
# codec, so it needs real gain rather than the card's default.
CAPTURE_CANDIDATES = ("Mic", "Capture", "Microphone", "Front Mic", "Internal Mic")
DEFAULT_CAPTURE_PCT = 80
CAPTURE_SWEEP = [40, 60, 70, 80, 90, 100]
# Healthy speech peaks. Below the floor it is inaudible; above the ceiling it
# clips, and clipping into a mono comms mic sounds far worse than it measures.
CAPTURE_TARGET_MIN = 0.25
CAPTURE_TARGET_MAX = 0.85

# Blink patterns from architecture.md §3.4, as (on_ms, off_ms) sequences.
PATTERNS = {
    "slow": [(550, 550)],
    "fast": [(125, 125)],
    "heartbeat": [(130, 1970)],
    "ap": [(200, 200), (200, 200), (500, 200)],
}


# ── LEDs ──────────────────────────────────────────────────────────────────────


class Leds:
    """Drives the 74HC(T)595 over SPI0, using CE1 as RCLK.

    spidev holds CS low for the transfer and releases it high afterwards, and
    that rising edge is exactly what the '595 latch needs.
    """

    def __init__(self):
        import spidev

        self.spi = spidev.SpiDev()
        self.spi.open(0, 1)  # CE1 → RCLK
        self.spi.max_speed_hz = 1_000_000
        self.spi.mode = 0
        self.byte = 0
        self.bits = load_led_bits()

    def write(self, value):
        self.byte = value & 0xFF
        self.spi.xfer2([self.byte])

    def set_bit(self, bit, on):
        self.write((self.byte | (1 << bit)) if on else (self.byte & ~(1 << bit)))

    def set_named(self, name, on):
        if name not in self.bits:
            raise KeyError(name)
        self.set_bit(self.bits[name], on)

    def all_off(self):
        self.write(0)

    def close(self):
        try:
            self.all_off()
            self.spi.close()
        except Exception:
            pass


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {}


def save_state(**kwargs):
    state = load_state()
    state.update(kwargs)
    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n")


def load_led_bits():
    saved = load_state().get("led_bits")
    if isinstance(saved, dict) and saved:
        return {k: int(v) for k, v in saved.items()}
    return dict(DEFAULT_LED_BITS)


def resolve_led_name(answer):
    """Map typed input to one LED name, exactly or by unambiguous prefix.

    Naive prefix matching is a trap here: "telex" is a prefix-collision with
    "telegraphy" on the first four characters, so a loose match silently files
    telex's bit under telegraphy and never records telex at all.
    """
    answer = answer.strip().lower()
    if answer in DEFAULT_LED_BITS:
        return answer
    matches = [name for name in DEFAULT_LED_BITS if name.startswith(answer)]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        print(f"    ({answer!r} is ambiguous: {', '.join(matches)} — type more)")
    return None


def run_led_walk(leds):
    """Light each '595 output in turn and record which LED actually lit."""
    print("\n── LED bit-walk ─────────────────────────────────────────────────")
    print("Each of the 8 shift-register outputs is lit in turn.")
    print("Answer with: telegraphy (orange) / telex (green) / voip (green)")
    print("             wifi (blue) / none\n")

    discovered = {}
    for bit in range(8):
        leds.write(1 << bit)
        answer = input(f"  bit {bit} (0b{1 << bit:08b}) → which LED? ").strip().lower()
        leds.all_off()
        if answer in ("", "none", "n"):
            continue
        name = resolve_led_name(answer)
        if name is None:
            print(f"    (unrecognised: {answer!r}, ignored — bit {bit} left unmapped)")
        else:
            discovered[name] = bit

    if not discovered:
        print("\n  No LEDs identified. Check the '595 power rail, SRCLR (→5V),")
        print("  OE (→GND), and that dtparam=spi=on is set.")
        return False

    print("\n  Discovered mapping:")
    for name, bit in sorted(discovered.items(), key=lambda kv: kv[1]):
        print(f"    {name:12s} → bit {bit}  (Q{'ABCDEFGH'[bit]})")

    missing = set(DEFAULT_LED_BITS) - set(discovered)
    if missing:
        print(f"\n  NOT FOUND: {', '.join(sorted(missing))}")

    if discovered != DEFAULT_LED_BITS:
        print("\n  Differs from the assumed mapping in architecture.md §3.2.")
    if input("\n  Save this mapping? [y/N] ").strip().lower() == "y":
        leds.bits = discovered
        save_state(led_bits=discovered)
        print(f"  Saved to {STATE_FILE.name}")

    return not missing


def run_led_named(leds):
    # Re-read the mapping on entry, so hand-editing wiring_test.json takes
    # effect without restarting the tool — it is loaded once at construction.
    leds.bits = load_led_bits()

    print("\n── LED by name ──────────────────────────────────────────────────")
    print("Commands:")
    print("  <name> on|off     by name, using the mapping below")
    print("  bit <0-7> on|off  by raw shift-register bit, ignoring all names")
    print("  all | none | map | reload | q")
    print(
        "\n  Current mapping (from "
        f"{STATE_FILE.name if STATE_FILE.exists() else 'built-in defaults'}):"
    )
    for name, bit in sorted(leds.bits.items(), key=lambda kv: kv[1]):
        print(f"    {name:12s} → bit {bit}  (Q{'ABCDEFGH'[bit]})")
    print()

    while True:
        cmd = input("  leds> ").strip().lower().split()
        if not cmd:
            continue
        if cmd[0] in ("q", "quit", "exit"):
            leds.all_off()
            return True
        if cmd[0] == "all":
            leds.write(sum(1 << b for b in leds.bits.values()))
            continue
        if cmd[0] in ("none", "off"):
            leds.all_off()
            continue
        if cmd[0] in ("map", "reload"):
            leds.bits = load_led_bits()
            for name, bit in sorted(leds.bits.items(), key=lambda kv: kv[1]):
                print(f"    {name:12s} → bit {bit}  (Q{'ABCDEFGH'[bit]})")
            continue
        # Raw bit addressing: the one command that cannot be confused by a
        # wrong or stale name mapping, so use it to establish ground truth.
        if cmd[0] == "bit" and len(cmd) == 3 and cmd[1].isdigit():
            bit = int(cmd[1])
            if 0 <= bit <= 7:
                leds.set_bit(bit, cmd[2] == "on")
            else:
                print("  bit must be 0-7")
            continue
        if len(cmd) == 2:
            name = resolve_led_name(cmd[0])
            if name and name in leds.bits:
                leds.set_named(name, cmd[1] == "on")
                continue
            if name:
                print(
                    f"  {name!r} has no bit in the current mapping — "
                    f"run the bit-walk, or use 'bit <n> on'"
                )
                continue
        print("  ?")


def run_led_patterns(leds):
    print("\n── Blink patterns ───────────────────────────────────────────────")
    print("Each pattern runs on all four LEDs for ~6 s. Judge the timings.\n")
    mask = sum(1 << b for b in leds.bits.values())

    for name, seq in PATTERNS.items():
        print(f"  {name} …")
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            for on_ms, off_ms in seq:
                leds.write(mask)
                time.sleep(on_ms / 1000)
                leds.write(0)
                time.sleep(off_ms / 1000)
                if time.monotonic() >= deadline:
                    break
        leds.all_off()
        time.sleep(0.4)

    print("\n  'flash' (50 ms, held visible 150 ms) — 10 events:")
    for _ in range(10):
        leds.write(mask)
        time.sleep(0.15)
        leds.write(0)
        time.sleep(0.35)
    return True


# ── Buttons ───────────────────────────────────────────────────────────────────


def run_buttons(duration=30, pins=None):
    """Show live pin levels, count presses, and measure contact bounce.

    Polling rather than gpiozero events: event dispatch latency would be
    indistinguishable from the bounce we are trying to measure.

    The live level display matters more than the event counting. If a button
    does nothing, "GPIO2 reads 1 even while pressed" is a diagnosis; "no
    presses detected" is just a shrug.
    """
    pins = pins or {"telegraphy": PIN_TELEGRAPHY, "ptt": PIN_PTT}

    print("\n── Button monitor ───────────────────────────────────────────────")
    print("  " + "   ".join(f"{n}=GPIO{p}" for n, p in pins.items()))
    print("  Both are active-low: the level should read 1 idle and 0 pressed.")
    print("  Ctrl-C to stop early.\n")

    try:
        from gpiozero import DigitalInputDevice
    except ImportError:
        print("  gpiozero not available — sudo apt install python3-gpiozero")
        return False

    try:
        inputs = {name: DigitalInputDevice(pin, pull_up=True) for name, pin in pins.items()}
    except Exception as e:
        print(f"  Could not open GPIO: {e}")
        print("  On Bookworm gpiozero needs a pin factory: sudo apt install python3-lgpio")
        return False

    factory = type(next(iter(inputs.values())).pin_factory).__name__
    print(f"  pin factory: {factory}\n")

    # gpiozero inverts for pull_up=True: .value is 1 when the pin is LOW, i.e.
    # when the button is pressed. The raw electrical level is the complement.
    def level(dev):
        return 0 if dev.value else 1

    stats = {
        name: {"presses": 0, "transitions": 0, "bounce_ms": [], "last": level(dev)}
        for name, dev in inputs.items()
    }
    pending = dict.fromkeys(inputs)

    start = time.monotonic()
    deadline = start + duration
    next_redraw = 0.0
    try:
        while time.monotonic() < deadline:
            now = time.monotonic()
            for name, dev in inputs.items():
                lvl = level(dev)
                st = stats[name]
                if lvl != st["last"]:
                    st["last"] = lvl
                    st["transitions"] += 1
                    if pending[name] is None:
                        pending[name] = now
                        if lvl == 0:
                            st["presses"] += 1
                            print(f"\n  {name:12s} PRESSED")
                        else:
                            print(f"\n  {name:12s} released")
                elif pending[name] is not None and now - pending[name] > 0.08:
                    # Settled: everything inside this window was bounce.
                    st["bounce_ms"].append((now - pending[name]) * 1000)
                    pending[name] = None

            if now >= next_redraw:
                next_redraw = now + 0.1
                live = "  ".join(
                    f"{name}=GPIO{pins[name]}:{stats[name]['last']}" for name in inputs
                )
                remaining = int(deadline - now)
                sys.stdout.write(f"\r  levels  {live}   ({remaining}s left) ")
                sys.stdout.flush()
            time.sleep(0.0005)
    except KeyboardInterrupt:
        pass

    print("\n\n  Results:")
    ok = True
    for name, st in stats.items():
        if st["presses"] == 0:
            ok = False
            stuck = "high (1)" if st["last"] == 1 else "low (0)"
            print(f"    {name:12s} NO presses detected — level stayed {stuck}")
            if pins[name] in (2, 3):
                print(f"                 GPIO{pins[name]} carries a FIXED 1.8k pull-up to")
                print("                 3V3 on the Pi. A button behind series")
                print("                 resistance cannot pull it below the logic")
                print("                 threshold — measure the pin while pressed.")
            continue
        extra = st["transitions"] - 2 * st["presses"]
        worst = max(st["bounce_ms"]) if st["bounce_ms"] else 0.0
        print(
            f"    {name:12s} {st['presses']} press(es), "
            f"{st['transitions']} transitions "
            f"({'clean' if extra <= 0 else f'{extra} extra = bounce'}), "
            f"worst settle {worst:.1f} ms"
        )

    print("\n  Debounce guidance: set the daemon's debounce above the worst")
    print("  settle time. Defaults are 20 ms (dedicated PTT) / 50 ms (shared).")

    for dev in inputs.values():
        dev.close()
    return ok

    for dev in inputs.values():
        dev.close()
    return ok


# ── Audio ─────────────────────────────────────────────────────────────────────


def find_usb_card():
    try:
        out = subprocess.run(["aplay", "-l"], capture_output=True, text=True).stdout
    except FileNotFoundError:
        return None
    for line in out.splitlines():
        m = re.match(r"card (\d+): (\S+)", line)
        if m and ("usb" in line.lower() or "UGREEN" in line):
            return int(m.group(1)), m.group(2)
    for line in out.splitlines():
        m = re.match(r"card (\d+): (\S+)", line)
        if m:
            return int(m.group(1)), m.group(2)
    return None


def find_mixer(card):
    """Return the name of a usable playback volume control on this card."""
    try:
        out = subprocess.run(
            ["amixer", "-c", str(card), "scontrols"], capture_output=True, text=True
        ).stdout
    except FileNotFoundError:
        return None
    names = re.findall(r"Simple mixer control '([^']+)'", out)
    for candidate in MIXER_CANDIDATES:
        if candidate in names:
            return candidate
    for name in names:
        probe = subprocess.run(
            ["amixer", "-c", str(card), "sget", name], capture_output=True, text=True
        ).stdout
        if "Playback" in probe and "%" in probe:
            return name
    return None


def find_capture_mixer(card):
    """Return (control, has_autogain) for the card's capture path."""
    try:
        out = subprocess.run(
            ["amixer", "-c", str(card), "scontrols"], capture_output=True, text=True
        ).stdout
    except FileNotFoundError:
        return None, False
    names = re.findall(r"Simple mixer control '([^']+)'", out)
    autogain = any("auto gain" in n.lower() for n in names)
    for candidate in CAPTURE_CANDIDATES:
        if candidate in names:
            return candidate, autogain
    for name in names:
        probe = subprocess.run(
            ["amixer", "-c", str(card), "sget", name], capture_output=True, text=True
        ).stdout
        if "Capture" in probe and "%" in probe:
            return name, autogain
    return None, autogain


def set_capture_volume(card, control, pct):
    pct = max(0, min(100, int(pct)))
    r = subprocess.run(
        ["amixer", "-M", "-q", "-c", str(card), "sset", control, f"{pct}%", "cap"],
        capture_output=True,
        text=True,
    )
    return r.returncode == 0


def set_autogain(card, on):
    """Cheap USB codecs often expose an AGC switch, which helps a weak mic."""
    try:
        out = subprocess.run(
            ["amixer", "-c", str(card), "scontrols"], capture_output=True, text=True
        ).stdout
    except FileNotFoundError:
        return False
    for name in re.findall(r"Simple mixer control '([^']+)'", out):
        if "auto gain" in name.lower():
            r = subprocess.run(
                ["amixer", "-q", "-c", str(card), "sset", name, "on" if on else "off"],
                capture_output=True,
                text=True,
            )
            return r.returncode == 0
    return False


def get_mixer_volume(card, control):
    """Current volume as a percentage on ALSA's mapped (perceptual) scale."""
    out = subprocess.run(
        ["amixer", "-M", "-c", str(card), "sget", control], capture_output=True, text=True
    ).stdout
    m = re.search(r"\[(\d+)%\]", out)
    return int(m.group(1)) if m else None


def set_mixer_volume(card, control, pct):
    """Set volume on the mapped scale, so a percentage feels like a percentage.

    The hardware mixer is the last stage before the speaker amp, which is why
    the project's ceiling lives here rather than in software gain.
    """
    pct = max(0, min(100, int(pct)))
    r = subprocess.run(
        ["amixer", "-M", "-q", "-c", str(card), "sset", control, f"{pct}%"],
        capture_output=True,
        text=True,
    )
    return r.returncode == 0


def write_tone(path, freq=880, seconds=2.0, rate=8000, volume=0.5):
    frames = array.array("h")
    for i in range(int(rate * seconds)):
        # Fade in/out to avoid the click that a hard square edge produces.
        env = min(1.0, i / (rate * 0.02), (rate * seconds - i) / (rate * 0.02))
        frames.append(int(32767 * volume * env * math.sin(2 * math.pi * freq * i / rate)))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(frames.tobytes())


def peak_level(path):
    try:
        with wave.open(str(path), "rb") as w:
            frames = w.readframes(w.getnframes())
    except Exception:
        return 0.0
    samples = array.array("h")
    samples.frombytes(frames[: len(frames) // 2 * 2])
    return (max(abs(s) for s in samples) / 32767) if samples else 0.0


def run_volume_sweep(card_idx, control, dev):
    """Find the loudest level that is safe and comfortable for this speaker.

    Steps upward from quiet and stops the moment it is told to, so the driver is
    never asked to sustain a level nobody has approved.
    """
    print("\n── Volume sweep ─────────────────────────────────────────────────")
    print("A 2 s tone plays at increasing levels, starting quiet.")
    print("Answer 'n' as soon as it is loud enough, distorted or buzzing —")
    print("the sweep stops there and the previous level becomes the ceiling.\n")

    tone = Path("/tmp/bipbox_sweep.wav")
    write_tone(tone, seconds=2.0)

    safe = VOLUME_SWEEP[0]
    for pct in VOLUME_SWEEP:
        if pct >= VOLUME_WARN_PCT:
            print(f"\n  ! {pct}% is above the {VOLUME_WARN_PCT}% caution threshold.")
            if input("    Continue? [y/N] ").strip().lower() != "y":
                break
        set_mixer_volume(card_idx, control, pct)
        print(f"  {pct:3d}% …", end=" ", flush=True)
        subprocess.run(["aplay", "-q", "-D", dev, str(tone)], capture_output=True)
        answer = input("still comfortable and clean? [Y/n/q] ").strip().lower()
        if answer in ("n", "no"):
            print(f"       → stopping. Last good level: {safe}%")
            break
        if answer in ("q", "quit"):
            break
        safe = pct
    else:
        print(f"  Reached the top of the sweep at {safe}%.")

    tone.unlink(missing_ok=True)

    print(f"\n  Suggested ceiling (volume_max_pct): {safe}%")
    if safe <= 20:
        print("  That is very low — consider soldering the 47R resistor in")
        print("  parallel with the 22R, i.e. attenuate in hardware rather than")
        print("  running the amp at the bottom of its range.")
    if input("  Save as the default ceiling? [y/N] ").strip().lower() == "y":
        save_state(volume_max_pct=safe, mixer_control=control)
        print(f"  Saved to {STATE_FILE.name}")
    return safe


def level_bar(level, width=40):
    """Render a peak level with the healthy window marked, so the number means something."""
    bar = ["-"] * width
    lo, hi = int(CAPTURE_TARGET_MIN * width), int(CAPTURE_TARGET_MAX * width)
    filled = min(width, int(level * width))
    for i in range(filled):
        bar[i] = "#"
    for i in (lo, hi):
        if 0 <= i < width and bar[i] == "-":
            bar[i] = "|"
    verdict = (
        "SILENT"
        if level < 0.01
        else "weak"
        if level < CAPTURE_TARGET_MIN
        else "CLIPPING"
        if level > 0.99
        else "hot"
        if level > CAPTURE_TARGET_MAX
        else "good"
    )
    return f"peak {level * 100:5.1f}%  [{''.join(bar)}]  {verdict}"


def run_capture_sweep(card_idx, control, dev, has_agc=False):
    """Find a capture gain that puts normal speech in the healthy window.

    The speakermic is a low-output element into a cheap codec, so the card's
    default capture level is usually far too low to be usable.
    """
    print("\n── Mic gain sweep ───────────────────────────────────────────────")
    print("At each step you get 3 s to speak normally, at the distance you")
    print("would actually hold the mic. Aim for the window marked with |.\n")

    if (
        has_agc
        and input("  An automatic gain control exists. Enable it? [y/N] ").strip().lower() == "y"
    ):
        set_autogain(card_idx, True)
        print("  AGC on.\n")

    rec = Path("/tmp/bipbox_cap.wav")
    results = {}
    for pct in CAPTURE_SWEEP:
        set_capture_volume(card_idx, control, pct)
        print(f"  {pct:3d}% — speak now…", end=" ", flush=True)
        subprocess.run(
            [
                "arecord",
                "-q",
                "-D",
                dev,
                "-f",
                "S16_LE",
                "-r",
                "8000",
                "-c",
                "1",
                "-d",
                "3",
                str(rec),
            ],
            capture_output=True,
        )
        level = peak_level(rec)
        results[pct] = level
        print(level_bar(level))
    rec.unlink(missing_ok=True)

    usable = {p: v for p, v in results.items() if CAPTURE_TARGET_MIN <= v <= CAPTURE_TARGET_MAX}
    if usable:
        # Highest gain still inside the window, for the best margin over noise.
        best = max(usable)
        print(f"\n  Best: {best}% (peak {usable[best] * 100:.0f}%)")
    else:
        best = max(results, key=lambda p: results[p])
        if results[best] < CAPTURE_TARGET_MIN:
            print(
                f"\n  Nothing reached the window — loudest was {best}% at "
                f"{results[best] * 100:.0f}%."
            )
            print("  The mic is under-driven at the hardware level. Check the mic")
            print("  wiring for your variant, and whether bias is reaching the")
            print("  element (wiring 2 needs the 1uF coupling cap on Ring 2).")
            if has_agc:
                print("  Enabling the AGC is also worth a try.")
        else:
            print(f"\n  Everything clipped; quietest usable is below {min(results)}%.")

    set_capture_volume(card_idx, control, best)
    if input(f"  Save {best}% as the capture default? [y/N] ").strip().lower() == "y":
        save_state(capture_pct=best, capture_control=control)
        print(f"  Saved to {STATE_FILE.name}")
    return best


def run_audio(volume_pct=None):
    print("\n── Audio ────────────────────────────────────────────────────────")
    card = find_usb_card()
    if not card:
        print("  No ALSA playback card found. Is the UGREEN adapter plugged in?")
        return False
    idx, name = card
    dev = f"plughw:{idx},0"
    print(f"  Using card {idx} ({name}) → {dev}")

    control = find_mixer(idx)
    original = get_mixer_volume(idx, control) if control else None
    if volume_pct is None:
        volume_pct = load_state().get("volume_max_pct", DEFAULT_VOLUME_PCT)

    if control:
        set_mixer_volume(idx, control, volume_pct)
        print(f"  Mixer '{control}' set to {volume_pct}% (was {original}%) — speaker-safe default")
    else:
        print("  ! No playback mixer control found; the card will play at")
        print("    whatever level it is already set to. Start quietly.")

    cap_control, has_agc = find_capture_mixer(idx)
    capture_pct = load_state().get("capture_pct", DEFAULT_CAPTURE_PCT)
    if cap_control:
        set_capture_volume(idx, cap_control, capture_pct)
        print(
            f"  Capture '{cap_control}' set to {capture_pct}%"
            + (" (AGC available)" if has_agc else "")
        )
    else:
        print("  ! No capture mixer control found — the mic runs at the card's default.")
    print()

    tone = Path("/tmp/bipbox_tone.wav")
    rec = Path("/tmp/bipbox_rec.wav")
    write_tone(tone)

    print("  1/2 Playing an 880 Hz tone (2 s) — listen on the speakermic.")
    r = subprocess.run(["aplay", "-q", "-D", dev, str(tone)], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"      aplay failed: {r.stderr.strip()[:200]}")
        if control and original is not None:
            set_mixer_volume(idx, control, original)
        return False
    heard = input("      Did you hear it? [y/N] ").strip().lower() == "y"

    print("\n  2/2 Recording 3 s from the mic — speak now.")
    r = subprocess.run(
        ["arecord", "-q", "-D", dev, "-f", "S16_LE", "-r", "8000", "-c", "1", "-d", "3", str(rec)],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        print(f"      arecord failed: {r.stderr.strip()[:200]}")
        print("      Note: the UGREEN mic input needs a TRS (3-pole) plug.")
        return False

    level = peak_level(rec)
    print(f"      {level_bar(level)}")
    if level < 0.01:
        print("      Silent — check the mic wiring (Ring 1 or Ring 2 per variant).")
    elif level < CAPTURE_TARGET_MIN:
        print("      Weak — run the mic gain sweep below.")
    elif level > 0.99:
        print("      Clipping — lower the capture gain.")

    print("      Playing it back…")
    subprocess.run(["aplay", "-q", "-D", dev, str(rec)], capture_output=True)
    back = input("      Did you hear your voice? [y/N] ").strip().lower() == "y"

    for f in (tone, rec):
        f.unlink(missing_ok=True)

    if cap_control and (
        level < CAPTURE_TARGET_MIN
        or input("\n  Run the mic gain sweep? [y/N] ").strip().lower() == "y"
    ):
        run_capture_sweep(idx, cap_control, dev, has_agc)

    if (
        control
        and input("\n  Run the volume sweep to find the ceiling? [y/N] ").strip().lower() == "y"
    ):
        run_volume_sweep(idx, control, dev)
    elif control and original is not None:
        set_mixer_volume(idx, control, original)
        print(f"  Mixer restored to {original}%.")

    return heard and back and level >= CAPTURE_TARGET_MIN


# ── Printer ───────────────────────────────────────────────────────────────────

ESC = b"\x1b"
GS = b"\x1d"


def find_printer():
    for pattern in ("/dev/usb/lp*", "/dev/ttyACM*"):
        for path in sorted(glob.glob(pattern)):
            if os.access(path, os.W_OK):
                return path
    return None


def run_printer():
    print("\n── Printer ──────────────────────────────────────────────────────")
    path = find_printer()
    if not path:
        print("  No writable printer node found (/dev/usb/lp* or /dev/ttyACM*).")
        print(f"  Checked as uid {os.geteuid()}. Try sudo, or check the USB connection.")
        print("  Note: bipboxd blacklists usblp and uses libusb instead, so")
        print("  /dev/usb/lp0 existing now is expected and fine for this test.")
        return False

    print(f"  Writing raw ESC/POS to {path}")
    out = bytearray()
    out += ESC + b"@"  # init
    out += ESC + b"a" + b"\x01"  # centre
    out += ESC + b"E" + b"\x01"  # bold on
    out += b"BIPBOX\n"
    out += ESC + b"E" + b"\x00"  # bold off
    out += b"wiring test\n"
    out += b"-" * 42 + b"\n"
    out += ESC + b"a" + b"\x00"  # left
    out += time.strftime("%d/%m/%Y %H:%M:%S").encode() + b"\n"
    out += f"device : {path}\n".encode()
    out += b"cols   : 42 (80mm)\n"
    out += b"-" * 42 + b"\n"
    out += b"0123456789" * 4 + b"01\n"  # 42-column ruler
    out += b"\n\n\n"
    out += GS + b"V" + b"\x00"  # full cut

    try:
        with open(path, "wb") as f:
            f.write(bytes(out))
            f.flush()
    except Exception as e:
        print(f"  Write failed: {e}")
        return False

    print("  Sent. The ruler line should be exactly the paper width.")
    return input("  Did it print correctly? [y/N] ").strip().lower() == "y"


def capture_sweep_entry():
    card = find_usb_card()
    if not card:
        print("\n  No ALSA card found.")
        return False
    idx, _ = card
    control, has_agc = find_capture_mixer(idx)
    if not control:
        print("\n  No capture mixer control on this card.")
        return False
    return bool(run_capture_sweep(idx, control, f"plughw:{idx},0", has_agc))


def volume_sweep_entry():
    card = find_usb_card()
    if not card:
        print("\n  No ALSA playback card found.")
        return False
    idx, _ = card
    control = find_mixer(idx)
    if not control:
        print("\n  No playback mixer control found on this card.")
        return False
    return bool(run_volume_sweep(idx, control, f"plughw:{idx},0"))


# ── Driver ────────────────────────────────────────────────────────────────────


def check_prereqs():
    problems = []
    if not glob.glob("/dev/spidev0.*"):
        problems.append("No /dev/spidev0.* — add 'dtparam=spi=on' to config.txt and reboot.")
    try:
        import spidev  # noqa: F401
    except ImportError:
        problems.append("python3-spidev missing — sudo apt install python3-spidev")
    try:
        import gpiozero  # noqa: F401
    except ImportError:
        problems.append("python3-gpiozero missing — sudo apt install python3-gpiozero")
    if os.geteuid() != 0:
        problems.append("Not running as root — some tests will fail. Use sudo.")
    return problems


def menu(leds):
    items = [
        ("LED bit-walk (discover the '595 mapping)", lambda: run_led_walk(leds)),
        ("LED by name (manual on/off)", lambda: run_led_named(leds)),
        ("LED blink patterns", lambda: run_led_patterns(leds)),
        ("Button monitor + bounce measurement", run_buttons),
        ("Audio (tone out, record, playback)", run_audio),
        ("Volume sweep (find the safe ceiling)", volume_sweep_entry),
        ("Mic gain sweep", capture_sweep_entry),
        ("Printer test ticket", run_printer),
    ]
    while True:
        print("\n╔══════════════════════════════════════════╗")
        print("║        BIPBOX — HAT wiring test          ║")
        print("╚══════════════════════════════════════════╝")
        for i, (label, _) in enumerate(items, 1):
            print(f"  {i}. {label}")
        print("  s. Full self-test")
        print("  q. Quit")
        choice = input("\n  > ").strip().lower()
        if choice in ("q", "quit", "exit"):
            return
        if choice == "s":
            selftest(leds)
            continue
        if choice.isdigit() and 1 <= int(choice) <= len(items):
            try:
                items[int(choice) - 1][1]()
            except KeyboardInterrupt:
                print("\n  (interrupted)")
            continue
        print("  ?")


def selftest(leds):
    print("\n═══ Full self-test ═══")
    results = {}
    for name, fn in (
        ("LEDs (bit-walk)", lambda: run_led_walk(leds)),
        ("Buttons", lambda: run_buttons(20)),
        ("Audio", run_audio),
        ("Printer", run_printer),
    ):
        try:
            results[name] = fn()
        except KeyboardInterrupt:
            results[name] = None
            print("\n  (skipped)")
        except Exception as e:
            results[name] = False
            print(f"  error: {e}")

    print("\n═══ Summary ═══")
    for name, ok in results.items():
        mark = "PASS" if ok else ("SKIP" if ok is None else "FAIL")
        print(f"  [{mark}] {name}")
    return all(v for v in results.values() if v is not None)


def main():
    ap = argparse.ArgumentParser(description="Bipbox HAT wiring test")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--leds", action="store_true")
    ap.add_argument("--buttons", action="store_true")
    ap.add_argument("--audio", action="store_true")
    ap.add_argument(
        "--volume-sweep", action="store_true", help="find the speaker-safe volume ceiling"
    )
    ap.add_argument(
        "--volume",
        type=int,
        metavar="PCT",
        help=f"playback volume for the audio test "
        f"(default {DEFAULT_VOLUME_PCT}%%, or the saved ceiling)",
    )
    ap.add_argument("--mic-sweep", action="store_true", help="find a usable capture gain")
    ap.add_argument(
        "--pin",
        type=int,
        action="append",
        metavar="N",
        help="monitor this GPIO instead of the defaults (repeatable)",
    )
    ap.add_argument("--printer", action="store_true")
    args = ap.parse_args()

    if args.volume is not None and not 0 <= args.volume <= 100:
        ap.error("--volume must be 0-100")

    for p in check_prereqs():
        print(f"  ! {p}")

    if args.buttons:
        pins = {f"gpio{p}": p for p in args.pin} if args.pin else None
        return 0 if run_buttons(pins=pins) else 1
    if args.volume_sweep:
        return 0 if volume_sweep_entry() else 1
    if args.mic_sweep:
        return 0 if capture_sweep_entry() else 1
    if args.audio:
        return 0 if run_audio(args.volume) else 1
    if args.printer:
        return 0 if run_printer() else 1

    try:
        leds = Leds()
    except Exception as e:
        print(f"\nCould not open SPI: {e}")
        if args.leds or args.selftest:
            return 1
        leds = None

    try:
        if args.leds:
            return 0 if (run_led_walk(leds) and run_led_patterns(leds)) else 1
        if args.selftest:
            return 0 if selftest(leds) else 1
        menu(leds)
        return 0
    finally:
        if leds:
            leds.close()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print()
        sys.exit(130)
