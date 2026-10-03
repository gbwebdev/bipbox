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
    sudo python3 wiring_test.py            interactive menu
    sudo python3 wiring_test.py --selftest  run everything, print a summary
    sudo python3 wiring_test.py --leds      LED tests only
    sudo python3 wiring_test.py --buttons   button monitor only
    sudo python3 wiring_test.py --audio     audio test only
    sudo python3 wiring_test.py --printer   printer test only
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
# conservative and never jumps to full scale.
DEFAULT_VOLUME_PCT = 40
VOLUME_WARN_PCT = 70
VOLUME_SWEEP = [10, 20, 30, 40, 50, 60, 70, 80]
MIXER_CANDIDATES = ("PCM", "Speaker", "Headphone", "Master", "Playback")

# Blink patterns from architecture.md §3.4, as (on_ms, off_ms) sequences.
PATTERNS = {
    "slow": [(500, 500)],
    "fast": [(100, 100)],
    "heartbeat": [(80, 1920)],
    "ap": [(150, 150), (150, 150), (450, 150)],
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
        for name in DEFAULT_LED_BITS:
            if name.startswith(answer) or answer.startswith(name[:4]):
                discovered[name] = bit
                break
        else:
            print(f"    (unrecognised: {answer!r}, ignored)")

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
    print("\n── LED by name ──────────────────────────────────────────────────")
    print("Commands: <name> on | <name> off | all | none | q")
    print(f"Names: {', '.join(leds.bits)}\n")
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
        if len(cmd) == 2 and cmd[0] in leds.bits:
            leds.set_named(cmd[0], cmd[1] == "on")
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


def run_buttons(duration=30):
    """Poll both buttons and measure contact bounce.

    Polling rather than gpiozero events: event dispatch latency would be
    indistinguishable from the bounce we are trying to measure.
    """
    print("\n── Button monitor ───────────────────────────────────────────────")
    print(f"Press TELEGRAPHY (GPIO{PIN_TELEGRAPHY}) and PTT (GPIO{PIN_PTT}).")
    print("Both are active-low. Ctrl-C to stop early.\n")

    try:
        from gpiozero import DigitalInputDevice
    except ImportError:
        print("  gpiozero not available — install python3-gpiozero.")
        return False

    # GPIO2 has an external 1.8k pull-up on the Pi; GPIO23 needs the internal one.
    try:
        inputs = {
            "telegraphy": DigitalInputDevice(PIN_TELEGRAPHY, pull_up=True),
            "ptt": DigitalInputDevice(PIN_PTT, pull_up=True),
        }
    except Exception as e:
        print(f"  Could not open GPIO: {e}")
        return False

    stats = {
        name: {"presses": 0, "transitions": 0, "bounce_ms": [], "last": dev.value}
        for name, dev in inputs.items()
    }
    pending = dict.fromkeys(inputs)

    deadline = time.monotonic() + duration
    try:
        while time.monotonic() < deadline:
            now = time.monotonic()
            for name, dev in inputs.items():
                v = dev.value
                st = stats[name]
                if v != st["last"]:
                    st["last"] = v
                    st["transitions"] += 1
                    if pending[name] is None:
                        pending[name] = now
                        if v == 0:
                            st["presses"] += 1
                            print(f"  {name:12s} PRESSED")
                        else:
                            print(f"  {name:12s} released")
                elif pending[name] is not None and now - pending[name] > 0.08:
                    # Settled: everything inside this window was bounce.
                    st["bounce_ms"].append((now - pending[name]) * 1000)
                    pending[name] = None
            time.sleep(0.0002)
    except KeyboardInterrupt:
        print()

    print("\n  Results:")
    ok = False
    for name, st in stats.items():
        if st["presses"] == 0:
            print(f"    {name:12s} no presses detected")
            continue
        ok = True
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
        print(
            f"  Mixer '{control}' set to {volume_pct}% (was {original}%) — speaker-safe default\n"
        )
    else:
        print("  ! No playback mixer control found; the card will play at")
        print("    whatever level it is already set to. Start quietly.\n")

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
    bar = "#" * int(level * 40)
    print(f"      peak level: {level * 100:5.1f}%  |{bar:<40}|")
    if level < 0.01:
        print("      Silent — check the mic wiring (Ring 1 or Ring 2 per variant).")
    elif level > 0.99:
        print("      Clipping — consider the 47R resistor in parallel.")

    print("      Playing it back…")
    subprocess.run(["aplay", "-q", "-D", dev, str(rec)], capture_output=True)
    back = input("      Did you hear your voice? [y/N] ").strip().lower() == "y"

    for f in (tone, rec):
        f.unlink(missing_ok=True)

    if (
        control
        and input("\n  Run the volume sweep to find the ceiling? [y/N] ").strip().lower() == "y"
    ):
        run_volume_sweep(idx, control, dev)
    elif control and original is not None:
        set_mixer_volume(idx, control, original)
        print(f"  Mixer restored to {original}%.")

    return heard and back and level >= 0.01


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
    ap.add_argument("--printer", action="store_true")
    args = ap.parse_args()

    if args.volume is not None and not 0 <= args.volume <= 100:
        ap.error("--volume must be 0-100")

    for p in check_prereqs():
        print(f"  ! {p}")

    if args.buttons:
        return 0 if run_buttons() else 1
    if args.volume_sweep:
        return 0 if volume_sweep_entry() else 1
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
