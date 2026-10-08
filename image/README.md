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
| WiFi credentials *(by default — see below)* | SSH host keys and `machine-id` (regenerated on first boot) |

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
sudo bash image/prepare-for-capture.sh              # keeps WiFi
sudo bash image/prepare-for-capture.sh --wipe-wifi  # for a shareable image
sudo shutdown -h now
```

### WiFi: kept by default, and why

Keeping the credentials is what makes a flashed card useful immediately — it
joins the bench network on its own and you can SSH straight in. **Never share
such an image**: NetworkManager stores the PSK in plain text.

Use `--wipe-wifi` for an image you intend to publish, or for a box destined
for another house, where your credentials would be wrong anyway. A card with
no known network falls back to AP mode and is configured from the local
console (§9.3) — which is the real deployment path for boxes that live
elsewhere.

Either way the script **always** deletes `custom.toml`, `userconf.txt`, `ssh`
and `wpa_supplicant.conf` from the boot partition. Those are consumed at first
boot, and they are far more exposed than anything under `/etc`: the boot
partition is FAT, so any computer can read them with no root and no ext4
driver — and `custom.toml` holds both the WiFi PSK and the user's password
hash in clear text. Leaving it would also re-apply its hostname on the next
first boot, fighting the per-box hostname set afterwards.

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

### If a flashed card joins WiFi but refuses SSH

That means **sshd has no host keys**. Connection *refused* rather than timing
out is the tell: the host is up and nothing is listening on 22.

Host keys must not be shared between boxes, so they are deleted during
preparation — but **sshd refuses to start with none**, and Raspberry Pi OS only
regenerates them as part of its first-boot flow, which a card that has already
booted never runs again. The script now installs and verifies a regeneration
unit *before* deleting anything, and aborts rather than producing an
unreachable image.

To rescue a card already in that state, generate the keys onto it offline:

```bash
sudo mount /dev/sdX2 /mnt
sudo ssh-keygen -q -t rsa     -b 3072 -f /mnt/etc/ssh/ssh_host_rsa_key     -N ''
sudo ssh-keygen -q -t ecdsa           -f /mnt/etc/ssh/ssh_host_ecdsa_key   -N ''
sudo ssh-keygen -q -t ed25519         -f /mnt/etc/ssh/ssh_host_ed25519_key -N ''
sudo umount /mnt
```

With console access, `sudo ssh-keygen -A && sudo systemctl restart ssh` is the
same fix, and `systemctl status ssh` shows
`sshd: no hostkeys available -- exiting`.

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
