# VoIP spike — Phase 0b

**Purpose:** decide whether Janus AudioBridge with plain-RTP participants is
viable on a Raspberry Pi Zero W, by measuring the two numbers that gate the
design (`../../docs/voip-options.md`, `../../docs/architecture.md` §6):

1. **CPU** on the Pi, as a percentage of its single ARMv6 core.
2. **Mouth-to-ear latency** — target < 150 ms, acceptable < 500 ms, fail above.

Nothing here is production code. What survives the spike moves into `device/`
and `server/` in phase 5; the Dockerfile is written to be production-grade
already, since we will need it anyway.

## Why this configuration

The Pi joins as a **plain RTP participant** — no WebRTC, no DTLS, no ICE, no
SRTP — and the room runs at **8 kHz with PCMU**, so the Pi performs *no codec
work at all*: mu-law is a lookup table. AudioBridge **mixes server-side**, so
the Pi decodes exactly one stream no matter how many people are in the room.

That combination is what makes an ARMv6 chip plausible. The spike exists to
confirm it rather than assume it.

## 1. Start Janus

On any machine on the same network as the Pi (your Mac is fine):

```bash
cd spike/voip
docker compose up --build
```

First build compiles Janus v1.4.2 from source — a few minutes. Then check it:

```bash
curl -s localhost:8088/janus/info | head -30          # should report version 1.4.2
curl -s localhost:8088/janus/info | grep audiobridge  # plugin must be present
```

> On a **Linux** host, prefer `network_mode: host` in `docker-compose.yml` and
> drop the `ports:` block. It avoids the userland UDP proxy and matches what
> production will do (bound to `wg0`).

## 2. Prepare the Pi

```bash
sudo apt install -y gstreamer1.0-tools gstreamer1.0-plugins-base \
                    gstreamer1.0-plugins-good gstreamer1.0-alsa
aplay -l    # note the USB card number for the --alsa argument below
```

Everything needed is a Debian package. Nothing is compiled, and there is no
Python audio stack — which is exactly the point of this design.

## 3. Join the room

```bash
python3 audiobridge_join.py \
    --janus http://<docker-host>:8088 \
    --room 1000 \
    --local-ip <pi-ip> --local-port 5004 \
    --display pi-a \
    --alsa plughw:1,0
```

It prints every Janus event verbatim, then the two GStreamer commands with the
discovered ports filled in. Keep it running — it holds the session open.

### Already verified locally (2026-10-03, Janus 1.4.2 / AudioBridge 0.0.13)

The server side of this spike was run end to end before you got it, so these
are facts rather than hopes:

- Both static rooms load, and **`allow_rtp_participants` is accepted as a static
  room option** — no need to create rooms over the API.
- The plain-RTP join succeeds and replies with:

  ```json
  "rtp": { "ip": "192.168.148.2", "port": 10002, "payload_type": 0 }
  ```

  So `payload_type` really is automatic for G.711, as documented.

- **The `ip` Janus reports is its own view of itself** — above, a Docker-internal
  address that the Pi cannot reach. Always send to the host you addressed the
  API on; the join script does this and warns when the two differ. This will
  matter again behind WireGuard.

- **Two independent RTP port ranges, which cost me a diagnosis to find.** The
  AudioBridge plugin allocates plain-RTP participant ports from
  `rtp_port_range` in **`janus.plugin.audiobridge.jcfg`**, defaulting to
  10000+, entirely separately from `media.rtp_port_range` in `janus.jcfg`
  (which governs ICE/WebRTC only). The core range was correctly applied as
  20000–20020 and the plugin still bound 10002. Both ranges are now set
  explicitly and both are published in `docker-compose.yml`.

  Had this gone unnoticed, the failure mode on your bench would have been
  *silent audio with no error in any log*.

## 4. Run the pipelines

Paste the RX command and leave it running. Paste the TX command in another
shell to "talk". Join from a second machine (a laptop with the same script, or
a browser) so there is someone to hear.

## 5. Measure

### CPU

With both pipelines running:

```bash
./measure_cpu.sh 30
```

Report the average and peak, the codec, the room's sampling rate, how many
*other* participants were in the room, and whether TX was active — CPU with
one talker differs from CPU with three.

### Latency — the two-box method (recommended)

You have two boxes, which makes this easy and avoids instrumenting anything:

1. Put both boxes side by side, both joined to the room.
2. Start a voice recording on your phone, placed between them.
3. Hold PTT on box A and make a sharp sound — a single clap close to the mic.
4. Open the recording in Audacity. You will see the clap twice: the direct
   acoustic sound, then box B's speaker reproducing it.
5. The offset between them **is** the mouth-to-ear latency, including ALSA,
   the network, the mixer and the speaker.

Repeat about ten times and take the median; a single clap is not a measurement.

This measures the real quantity end to end, which no amount of pipeline
instrumentation does.

### Then repeat at 16 kHz with Opus

```bash
python3 audiobridge_join.py ... --room 1016 --codec opus
```

Compare both numbers. If PCMU is comfortable and Opus is not, we ship PCMU and
the question is closed. If both are comfortable, Opus at 16 kHz is the nicer
sound and we take it.

## 6. Record the outcome

Write the numbers into `RESULTS.md` next to this file:

| Codec | Room rate | Others in room | TX active | Avg CPU | Peak CPU | Latency (median) |
|---|---|---|---|---|---|---|

**Decision rule, agreed in advance** so the result is not argued after the
fact:

- CPU **< 50%** of one core with two other participants, and median latency
  **< 300 ms** → the design is confirmed, proceed with Janus AudioBridge.
- CPU 50–75%, or latency 300–500 ms → viable but tight; drop to PCMU if not
  already, lower the jitter buffer, and re-measure before committing.
- CPU **> 75%**, or latency **> 500 ms** → fall back, in order: Asterisk
  ConfBridge + baresip, then a custom mixer (`voip-options.md`).

## What this spike does *not* cover

Deliberately out of scope, to keep it answering one question:

- WireGuard — adds a known-small overhead and no new unknowns. Run on the LAN.
- Browser participants — a separate question (architecture.md §6.4).
- PTT, half-duplex muting, the bip, LED states — all phase 5.
- Any bipbox code at all.
