#!/usr/bin/env bash
# Strip a bipbox card of everything box-specific, ready to be captured as a
# golden image. RUN ON THE PI, as root, immediately before shutting down.
#
#   sudo bash prepare-for-capture.sh
#
# What makes this necessary: an image cloned without stripping identity gives
# every box the same SSH host keys and the same /etc/machine-id. Identical
# machine-ids make NetworkManager derive the same DHCP client identifier, so
# two boxes on one network fight over a lease — which presents as "the second
# box won't appear", a day of debugging away from its cause.
#
# Deliberately NOT zero-filling free space the way telex's prepare-image.sh
# did: capture.sh shrinks the filesystem instead, so unused space never enters
# the image at all. Faster, and it avoids writing the whole card for nothing.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Run with sudo." >&2
    exit 1
fi

if [[ ! -f /boot/firmware/config.txt ]]; then
    echo "This does not look like a Raspberry Pi OS system (no /boot/firmware/config.txt)." >&2
    echo "Refusing to run: this script deletes host keys and identity." >&2
    exit 1
fi

echo
echo "This card will be stripped of all box-specific identity."
echo "It should then be shut down and captured, NOT booted again as a box."
echo
read -rp "Continue? [y/N] " ok
[[ "${ok,,}" == "y" ]] || { echo "Cancelled."; exit 0; }
echo

# ── Per-box calibration ───────────────────────────────────────────────────────
# The LED bit order depends on how each harness was crimped, so baking box A's
# calibration into the image would light the wrong lamps on box B
# (architecture.md §3.2).
for f in /etc/bipbox/calibration.json /etc/bipbox/config.json /etc/bipbox/state.json \
         /home/pi/wiring_test.json; do
    [[ -e "$f" ]] && { rm -f "$f"; echo "✓ removed $f"; }
done

# ── Host identity ─────────────────────────────────────────────────────────────
rm -f /etc/ssh/ssh_host_*
echo "✓ SSH host keys removed (regenerated on first boot)"

# machine-id must be left EMPTY, not deleted: systemd regenerates an empty
# file, but a missing one can leave the system without an id at all.
: > /etc/machine-id
[[ -f /var/lib/dbus/machine-id ]] && : > /var/lib/dbus/machine-id
echo "✓ machine-id cleared (prevents DHCP lease collisions between boxes)"

rm -f /etc/NetworkManager/system-connections/*.nmconnection.bak 2>/dev/null || true

# ── Logs, caches, history ─────────────────────────────────────────────────────
journalctl --rotate >/dev/null 2>&1 || true
journalctl --vacuum-time=1s >/dev/null 2>&1 || true
find /var/log -type f \( -name '*.gz' -o -name '*.[0-9]' -o -name '*.old' \) -delete 2>/dev/null || true
find /var/log -type f -exec truncate -s 0 {} + 2>/dev/null || true
echo "✓ logs cleared"

apt-get clean
rm -rf /var/lib/apt/lists/*
echo "✓ apt cache cleared"

rm -f /root/.bash_history /home/*/.bash_history
rm -rf /root/.cache /home/*/.cache /tmp/* /var/tmp/*
echo "✓ history and caches cleared"

# ── Summary ───────────────────────────────────────────────────────────────────
USED=$(df -h --output=used / | tail -1 | tr -d ' ')
echo
echo "Stripped. Root filesystem now uses $USED."
echo
echo "Next:"
echo "  1. sudo shutdown -h now"
echo "  2. Move the card to a machine with a reader"
echo "  3. sudo bash image/capture.sh /dev/sdX bipbox-base"
echo
echo "WiFi credentials in /etc/NetworkManager were left in place."
echo "They are convenient for your own boxes, but do NOT publish an image"
echo "made from this card."
