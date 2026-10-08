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
#
# WiFi credentials are KEPT by default: a flashed card then joins the bench
# network on its own, which is the main reason to have a golden image at all.
# Pass --wipe-wifi for an image you intend to share, or for a box going to a
# different house (where the credentials would be wrong anyway).
set -euo pipefail

WIPE_WIFI=0
for arg in "$@"; do
    case "$arg" in
        --wipe-wifi) WIPE_WIFI=1 ;;
        -h|--help)
            echo "Usage: sudo bash prepare-for-capture.sh [--wipe-wifi]"
            exit 0
            ;;
        *) echo "Unknown option: $arg" >&2; exit 1 ;;
    esac
done

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
# Host keys must not be shared between boxes, but sshd REFUSES TO START with
# none — so removing them without arranging regeneration leaves a box that
# joins WiFi and then refuses SSH with a connection reset. Raspberry Pi OS only
# regenerates them as part of its first-boot flow, which a card that has
# already booted will never run again.
#
# So: install a regeneration unit FIRST, verify it is in place, and only then
# delete the keys.
if systemctl list-unit-files 2>/dev/null | grep -q '^regenerate_ssh_host_keys'; then
    systemctl enable regenerate_ssh_host_keys.service >/dev/null 2>&1 || true
    echo "✓ enabled Raspberry Pi OS's regenerate_ssh_host_keys.service"
else
    cat > /etc/systemd/system/bipbox-regen-ssh-keys.service <<'UNIT'
[Unit]
Description=Regenerate SSH host keys when absent
Before=ssh.service
ConditionPathExists=!/etc/ssh/ssh_host_ed25519_key

[Service]
Type=oneshot
ExecStart=/usr/bin/ssh-keygen -A

[Install]
WantedBy=multi-user.target
UNIT
    systemctl daemon-reload
    systemctl enable bipbox-regen-ssh-keys.service >/dev/null 2>&1
    echo "✓ installed bipbox-regen-ssh-keys.service"
fi

if ! systemctl is-enabled regenerate_ssh_host_keys.service >/dev/null 2>&1 \
   && ! systemctl is-enabled bipbox-regen-ssh-keys.service >/dev/null 2>&1; then
    echo "!! No SSH host key regeneration is enabled." >&2
    echo "!! Refusing to delete the host keys — doing so would make the image" >&2
    echo "!! boot into a box that joins WiFi and then refuses SSH." >&2
    exit 1
fi

rm -f /etc/ssh/ssh_host_*
echo "✓ SSH host keys removed (regenerated on first boot)"

# machine-id must be left EMPTY, not deleted: systemd regenerates an empty
# file, but a missing one can leave the system without an id at all.
: > /etc/machine-id
[[ -f /var/lib/dbus/machine-id ]] && : > /var/lib/dbus/machine-id
echo "✓ machine-id cleared (prevents DHCP lease collisions between boxes)"

# ── Provisioning files on the boot partition ──────────────────────────────────
# These are consumed at first boot and must not survive into an image. They sit
# on the FAT partition, so unlike anything under /etc they are readable by
# anyone who puts the card in any computer -- no root, no ext4 driver. custom.toml
# holds the WiFi PSK and the user's password hash in plain text.
#
# Leaving custom.toml would also re-apply its hostname on the next first boot,
# fighting the per-box hostname we set afterwards.
for f in /boot/firmware/custom.toml /boot/firmware/userconf.txt \
         /boot/firmware/ssh /boot/firmware/wpa_supplicant.conf; do
    [[ -e "$f" ]] && { rm -f "$f"; echo "✓ removed $f (plaintext credentials on a FAT partition)"; }
done

# NetworkManager runtime state is per-box, not configuration.
rm -f /etc/NetworkManager/system-connections/*.nmconnection.bak 2>/dev/null || true
rm -f /var/lib/NetworkManager/*.lease /var/lib/NetworkManager/secret_key \
      /var/lib/NetworkManager/timestamps /var/lib/NetworkManager/seen-bssids 2>/dev/null || true
echo "✓ NetworkManager runtime state cleared"

if (( WIPE_WIFI )); then
    rm -f /etc/NetworkManager/system-connections/*.nmconnection
    rm -f /etc/wpa_supplicant/wpa_supplicant*.conf 2>/dev/null || true
    echo "✓ WiFi credentials REMOVED (--wipe-wifi)"
fi

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
if (( WIPE_WIFI )); then
    echo "WiFi credentials were removed. A card flashed from this image will"
    echo "find no network and fall back to AP mode, where the local console"
    echo "configures it (architecture.md §9.3)."
else
    echo "WiFi credentials in /etc/NetworkManager were KEPT, so a flashed card"
    echo "joins your bench network by itself. Do NOT share this image."
    echo "Re-run with --wipe-wifi for one you intend to publish, or for a box"
    echo "going to another house where those credentials are wrong anyway."
fi
