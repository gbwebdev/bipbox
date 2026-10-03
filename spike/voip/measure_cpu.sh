#!/usr/bin/env bash
# Sample CPU usage of the GStreamer pipelines on the Pi.
#
# Reads /proc directly rather than requiring sysstat, so it works on a bare
# Raspberry Pi OS Lite. Reports per-process and total CPU as a percentage of
# ONE core — which is the number that matters, the Pi Zero W having exactly one.
#
#   ./measure_cpu.sh [seconds]
set -euo pipefail

DURATION="${1:-30}"
HZ=$(getconf CLK_TCK)

pids=$(pgrep -d' ' gst-launch-1.0 || true)
if [[ -z "${pids// /}" ]]; then
    echo "No gst-launch-1.0 processes running. Start the TX/RX pipelines first." >&2
    exit 1
fi

read_jiffies() {
    local total=0
    for pid in $1; do
        [[ -r "/proc/$pid/stat" ]] || continue
        # Fields 14 (utime) and 15 (stime), after the comm field which may
        # contain spaces — so split on the last ')'.
        local rest
        rest=$(sed 's/.*) //' "/proc/$pid/stat")
        local utime stime
        utime=$(echo "$rest" | cut -d' ' -f12)
        stime=$(echo "$rest" | cut -d' ' -f13)
        total=$((total + utime + stime))
    done
    echo "$total"
}

echo "Sampling $(echo "$pids" | wc -w | tr -d ' ') pipeline process(es) for ${DURATION}s…"
echo "(percentages are of one core; the Pi Zero W has one)"
echo

start=$(read_jiffies "$pids")
start_time=$(date +%s)

peak=0
while (( $(date +%s) - start_time < DURATION )); do
    before=$(read_jiffies "$pids")
    sleep 2
    after=$(read_jiffies "$pids")
    pct=$(awk -v d=$((after - before)) -v hz="$HZ" 'BEGIN{printf "%.1f", d/hz/2*100}')
    peak=$(awk -v p="$peak" -v c="$pct" 'BEGIN{print (c>p)?c:p}')
    printf "  instantaneous: %5s%%   load: %s\n" "$pct" "$(cut -d' ' -f1 /proc/loadavg)"
done

end=$(read_jiffies "$pids")
elapsed=$(( $(date +%s) - start_time ))
avg=$(awk -v d=$((end - start)) -v hz="$HZ" -v t="$elapsed" 'BEGIN{printf "%.1f", d/hz/t*100}')

echo
echo "────────────────────────────────────────"
echo "  average: ${avg}% of one core"
echo "  peak:    ${peak}%"
echo "  temp:    $(awk '{printf "%.1f°C", $1/1000}' /sys/class/thermal/thermal_zone0/temp 2>/dev/null || echo n/a)"
echo "────────────────────────────────────────"
echo
echo "Record this in spike/voip/RESULTS.md together with the codec, the room"
echo "sampling rate, the number of other participants, and whether TX was active."
