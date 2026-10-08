#!/usr/bin/env bash
# Capture a prepared bipbox card into a small, re-flashable image.
# RUN ON A LINUX MACHINE WITH A CARD READER, as root. Not on the Pi.
#
#   sudo bash capture.sh /dev/sdc bipbox-base
#
# Two things make the result small enough to keep and quick to write:
#
#   1. Only the sectors up to the end of the last partition are read. A 32 GB
#      card whose partitions end at 3 GB is read in 3 GB, not 32.
#   2. PiShrink then shrinks the root filesystem to its contents and installs a
#      first-boot hook that expands it again to fill whatever card it lands on.
#
# Without step 2 the image is as large as the card the rootfs was expanded onto,
# which is the whole reason golden images get abandoned.
set -euo pipefail

DEV="${1:-}"
NAME="${2:-bipbox-base}"
PISHRINK_URL="https://raw.githubusercontent.com/Drewsif/PiShrink/master/pishrink.sh"

if [[ $EUID -ne 0 ]]; then
    echo "Run with sudo." >&2
    exit 1
fi

usage() {
    echo "Usage: sudo bash capture.sh /dev/sdX [name]" >&2
    echo >&2
    lsblk -d -o NAME,SIZE,RM,MODEL | sed 's/^/  /' >&2
    exit 1
}

[[ -n "$DEV" ]] || usage
[[ -b "$DEV" ]] || { echo "$DEV is not a block device." >&2; usage; }

for tool in sfdisk dd python3 losetup e2fsck resize2fs parted xz; do
    command -v "$tool" >/dev/null || {
        echo "Missing $tool. Install: sudo apt install util-linux parted e2fsprogs xz-utils" >&2
        exit 1
    }
done

# ── Confirm the target ────────────────────────────────────────────────────────
echo
lsblk -o NAME,SIZE,RM,MODEL,MOUNTPOINTS "$DEV"
echo

if lsblk -no MOUNTPOINTS "$DEV" | grep -q .; then
    echo "Something on $DEV is mounted. Unmount it first:" >&2
    echo "  sudo umount ${DEV}?*" >&2
    exit 1
fi

read -rp "Capture this device? [y/N] " ok
[[ "${ok,,}" == "y" ]] || { echo "Cancelled."; exit 0; }

# ── Read only as far as the partitions actually go ────────────────────────────
read -r SECTOR_SIZE END_SECTOR < <(
    sfdisk --json "$DEV" | python3 -c '
import json, sys
t = json.load(sys.stdin)["partitiontable"]
parts = t.get("partitions") or []
if not parts:
    sys.exit("no partition table found")
print(t.get("sectorsize", 512), max(p["start"] + p["size"] for p in parts))
'
)
BYTES=$(( END_SECTOR * SECTOR_SIZE ))

IMG="${NAME}-$(date +%Y%m%d).img"
echo
printf 'Reading %s up to the end of its last partition (%.2f GB)…\n' \
    "$DEV" "$(python3 -c "print($BYTES/1e9)")"
echo

dd if="$DEV" of="$IMG" bs=4M iflag=count_bytes count="$BYTES" \
   conv=fsync status=progress

# ── Shrink ────────────────────────────────────────────────────────────────────
if [[ ! -x ./pishrink.sh ]]; then
    echo
    echo "Fetching PiShrink from $PISHRINK_URL"
    echo "(review it if you like — it is a shell script and it runs as root)"
    curl -fsSL "$PISHRINK_URL" -o pishrink.sh
    chmod +x pishrink.sh
fi

echo
echo "Shrinking and compressing…"
./pishrink.sh -Z "$IMG"          # -Z = xz-compress the result

RESULT="${IMG}.xz"
[[ -f "$RESULT" ]] || RESULT="$IMG"

echo
echo "────────────────────────────────────────────────────────────"
echo "  Image:  $RESULT"
echo "  Size:   $(du -h "$RESULT" | cut -f1)"
echo
echo "  Write it to a new card with:"
echo "    xz -dc $RESULT | sudo dd of=/dev/sdX bs=4M conv=fsync status=progress"
echo
echo "  Then verify, because this is how the last card wasted an evening:"
echo "    sudo cmp -n \$(xz -l --robot $RESULT | awk '/totals/{print \$5}') \\"
echo "        <(xz -dc $RESULT) /dev/sdX && echo VERIFIED"
echo
echo "  The root filesystem expands to fill the card on first boot."
echo "  Per-box setup still needed afterwards: hostname, and the bit-walk"
echo "  from device/tools/wiring_test.py to produce that box's calibration."
echo "────────────────────────────────────────────────────────────"
