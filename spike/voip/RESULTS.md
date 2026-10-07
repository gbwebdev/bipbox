# VoIP spike — results

Fill this in as you measure. The decision rule is in `README.md` and was agreed
before any numbers existed, so the outcome is not argued after the fact.

## Server side — done

Verified 2026-10-03 on an arm64 Docker host, Janus **1.4.2**, AudioBridge
**0.0.13**:

| Check | Result |
|---|---|
| Janus builds from pinned source | ✅ |
| `audiobridge` plugin loads | ✅ 0.0.13 |
| Static rooms with `allow_rtp_participants` | ✅ accepted, no API creation needed |
| Plain-RTP join, 8 kHz room, PCMU | ✅ `payload_type` 0 assigned automatically |
| Plain-RTP join, 16 kHz room, Opus | ✅ `payload_type` 111 accepted |
| Plugin RTP ports inside the configured range | ✅ after setting the *plugin's* own `rtp_port_range` |

## Device side — to measure on a Pi Zero W

| Codec | Room rate | Others in room | TX active | Avg CPU | Peak CPU | Latency (median of 10) | Notes |
|---|---|---|---|---|---|---|---|
| PCMU | 8000 | 1 | no | | | | |
| PCMU | 8000 | 1 | yes | | | | |
| PCMU | 8000 | 2 | yes | | | | |
| Opus | 16000 | 1 | yes | | | | |
| Opus | 16000 | 2 | yes | | | | |

CPU is a percentage of **one** core — the Pi Zero W has exactly one.

### Jitter buffer

The `rtpjitterbuffer latency` default in the spike is 60 ms. If audio is clean,
try lowering it; if it is choppy, raise it. Record what you settle on, because
it is the single largest tunable contributor to latency.

| latency (ms) | Audio quality | Measured mouth-to-ear |
|---|---|---|
| 60 | | |

### Observations

<!-- Anything surprising: dropouts, clock drift, distortion, thermal effects
     after a few minutes, behaviour when the Pi's WiFi is weak. -->

## Decision

<!-- Confirmed / tight / fall back — per the rule in README.md. -->
