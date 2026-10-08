# Golden image workflow

Capturing a prepared card and re-flashing it, so setting up a box is minutes
rather than an evening. This is the interim approach; `pi-gen` (phase 6) will
eventually build the image from source instead, which is reproducible and
diffable where a cloned card is neither.

## What belongs in the image, and what does not

| In the image | Per box, applied after flashing |
|---|---|
| Raspberry Pi OS Lite (Bookworm, **armhf** — the Pi Zero W is ARMv6) | hostname |
| `config.txt` | device UUID and server credentials |
| apt packages | **calibration** — the LED bit order is a property of each harness (§3.2) |
| WiFi credentials *(convenient, but never publish such an image)* | SSH host keys and `machine-id` (regenerated on first boot) |

Getting that split wrong is not a tidiness issue. Two boxes sharing a
`machine-id` derive the same DHCP client identifier and fight over a lease,
which presents as "the second box never appears on the network" — a long way
from its cause. And box A's calibration on box B lights the wrong lamps.

## 1. Prepare the card you want to clone

Install everything slow, so you never wait for it again. On a Pi Zero these
take a while:

```bash
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y \
    python3-spidev python3-gpiozero python3-lgpio \
    python3-venv libusb-1.0-0 \
    gstreamer1.0-tools gstreamer1.0-plugins-base \
    gstreamer1.0-plugins-good gstreamer1.0-alsa alsa-utils \
    avahi-daemon git
```

Settle `config.txt` (§3.1) and confirm the box boots and joins WiFi **before**
capturing. An image made from a card you have not booted is a guess.

## 2. Strip box-specific identity

On the Pi, as root, immediately before shutting down:

```bash
sudo bash image/prepare-for-capture.sh
sudo shutdown -h now
```

## 3. Capture

Move the card to a machine with a reader (not the Pi — a live root filesystem
cannot be imaged cleanly):

```bash
sudo bash image/capture.sh /dev/sdX bipbox-base
```

It reads only as far as the last partition, then uses
[PiShrink](https://github.com/Drewsif/PiShrink) to shrink the root filesystem
and install a first-boot hook that expands it again on whatever card it lands
on. A 32 GB card yields a few hundred megabytes rather than 32 GB — which is
the difference between a golden image you keep and one you abandon.

## 4. Write and verify

```bash
xz -dc bipbox-base-YYYYMMDD.img.xz | sudo dd of=/dev/sdX bs=4M conv=fsync status=progress
sync
```

**Always verify.** An unverified write cost an evening during bring-up, and the
failure is silent:

```bash
sudo blockdev --flushbufs /dev/sdX
echo 3 | sudo tee /proc/sys/vm/drop_caches >/dev/null
SIZE=$(xz -l --robot bipbox-base-YYYYMMDD.img.xz | awk '/totals/{print $5}')
sudo cmp -n "$SIZE" <(xz -dc bipbox-base-YYYYMMDD.img.xz) /dev/sdX && echo VERIFIED
```

New cards are worth checking once before you trust them at all:

```bash
sudo f3probe --destructive --time-ops /dev/sdX
```

## 5. Per-box setup after flashing

1. Set the hostname (`bipbox-<name>`).
2. Run the bit-walk — **each box needs its own**, because the two green lamps
   sit on identical 300 Ω positions and the assignment comes from how the
   harness was crimped, not from the PCB:
   ```bash
   sudo python3 device/tools/wiring_test.py --leds
   ```
3. Install the resulting `wiring_test.json` as `/etc/bipbox/calibration.json`.
4. Enter the server URL and device credentials.

## Keep a spare

Keep one card flashed and ready. It turns a dead card from an evening of
diagnosis into a sixty-second swap, and cards do die — two did during bring-up.
