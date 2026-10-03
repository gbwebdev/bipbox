# Bipbox — VoIP transport: options surveyed

Date: 2026-10-02. Supersedes the Mumble recommendation in `open-questions.md` Q5b.

**Disclosure:** my first recommendation (Mumble) was not the result of a survey —
it was an evaluation of the one option you happened to name. This document is
the survey, and it reaches a different conclusion. Mumble turns out to be close
to the *worst* viable option for this specific board.

---

## The decisive constraint

The Raspberry Pi Zero W (2017) is a **single-core 1 GHz ARM1176JZF-S: ARMv6,
VFPv2, and no NEON**. Every Opus benchmark you will read online is from ARMv7 or
aarch64 hardware with NEON SIMD; none of those numbers transfer. On this chip
Opus runs on scalar floating point.

So the question that ranks every option is:

> **How many Opus frames must the Pi encode and decode per second, and at what
> sampling rate?**

Which decomposes into two architectural properties of the server:

1. **Does the server mix (MCU) or merely forward (SFU/relay)?**
   Forwarding means the Pi decodes one stream *per speaker*. Mixing means it
   decodes exactly **one**, forever, regardless of how many boxes exist.
2. **What sampling rate is imposed?** 48 kHz costs roughly 3× what 16 kHz does,
   and G.711 at 8 kHz costs essentially nothing (µ-law is a table lookup).

A secondary constraint, from D3: the audio path must work over **UDP to your
static IP inside a WireGuard tunnel**, with nothing in public DNS. Note that
with both boxes behind home NAT, WireGuard is necessarily **hub-and-spoke
through your server** — so the server is in the media path no matter what we
choose. Given that it is already there, having it *mix* is free architecture.
This kills the "mesh, no media server" idea on its own terms.

---

## Options

### 1. Janus Gateway — AudioBridge plugin — **recommended**

Janus is a C WebRTC server from Meetecho; its `audiobridge` plugin is a
**server-side audio mixer** (a real MCU, not an SFU).

Verified against the plugin documentation:

- It **mixes server-side and sends each participant a single mixed stream**.
- It supports **plain RTP participants** — joining the mix by exchanging bare
  IP/port, with **no WebRTC, no DTLS, no ICE, no SRTP on the Pi at all**. Enabled
  per room with `allow_rtp_participants = true`; the join request carries an
  `rtp` object with `ip`, `port`, and optionally `payload_type`, `audiolevel_ext`,
  `fec`. (The object is called `rtp` — there is no `rtp_participant`, a common
  error in blog posts.)
- Per-participant codec choice of **`opus`, `pcma` (A-law) or `pcmu` (µ-law)`**,
  with payload-type handling automatic for G.711.
- Room `sampling_rate` is configurable — 8000 and 16000 are both supported
  (16000 is the documented default for wideband mixing).

What that buys us on this board:

| Choice | Pi-side codec work |
|---|---|
| 8 kHz room, Pi joins as PCMU plain-RTP participant | **~zero** — µ-law is a lookup table |
| 16 kHz room, Pi joins as Opus plain-RTP participant | 1 encode + 1 decode @ 16 kHz |

Browser clients join **the same room** over native WebRTC with no bridge, no
second server, and no extra code — which removes most of the cost that made
browser VoIP a v2 item (Q43).

Pi-side implementation: a GStreamer pipeline, entirely from Debian armhf
packages — `alsasrc ! mulawenc ! rtppcmupay ! udpsink` out, and
`udpsrc ! rtppcmudepay ! rtpjitterbuffer ! mulawdec ! alsasink` in. **We do not
write a jitter buffer** — `rtpjitterbuffer` is a mature, tunable element. Janus
is driven over its HTTP/WebSocket API from our daemon.

Costs: Janus is another server component to run and understand (Docker image
exists); the plain-RTP participant path is less travelled than the WebRTC one,
so it needs validating early.

### 2. Asterisk (or FreeSWITCH) conference + a SIP endpoint on the Pi

`ConfBridge` also mixes server-side, G.711 and Opus both available, and browser
clients work via WebRTC-over-SIP (`chan_pjsip` + JsSIP/SIP.js). The Pi runs
`baresip` (tiny, packaged for armhf, scriptable command interface) or PJSIP.

Pros: the most battle-tested conferencing software in existence; server-side
mixing; G.711 means ~zero Pi codec cost; dial-in from a real phone becomes
possible if you ever want it.

Cons: SIP brings a large conceptual surface — dialplans, registrations, NAT
handling — for what is fundamentally three peers in one room. Significantly more
configuration to get right and to document than Janus AudioBridge, and the
browser path needs a second library.

**Verdict: a credible fallback, not the first choice.** Choose it if Janus's
plain-RTP path disappoints.

### 3. Mumble + `pymumble` — **no longer recommended**

Two findings disqualify it for *this* board:

- **Mumble's protocol is 48 kHz Opus only**, and it is a **forwarding** server,
  not a mixer — the client decodes one stream per speaker. So this is the single
  most CPU-expensive option of the lot, on the weakest CPU.
- The talKKonnect project — people who build Mumble-based PTT radios on
  Raspberry Pis, i.e. exactly this use case — report that **48 kHz Opus on a
  single-core ARMv6 Pi Zero runs at nearly 100% CPU utilisation**. They
  additionally have to force Opus (`opusthreshold=0`) to avoid the even more
  expensive CELT path.
- **`pymumble` has no UDP media at all** — it works only through Mumble's TCP
  tunnel (the protocol's fallback mode). That is head-of-line blocking on the
  voice path, which directly contradicts D5's "minimise latency".

What I had credited it with — the adaptive jitter buffer — we get from
GStreamer's `rtpjitterbuffer` anyway, without the 48 kHz tax.

(A Go client such as `talkkonnect`/`gumble` would beat CPython, but the 48 kHz
protocol floor and the client-side-mixing topology are inherent to Mumble, not
to the client language. And it would put Go in a Python project.)

**Verdict: rejected.** It would be a fine choice on a Pi Zero 2 W or a Pi 3+.

### 4. WebRTC SFU — Jitsi / LiveKit / mediasoup

All three forward rather than mix, so the Pi pays per-speaker decodes at 48 kHz,
*and* must terminate DTLS-SRTP + ICE. LiveKit's client SDKs ship prebuilt Rust
binaries for x86_64/aarch64 — nothing for ARMv6, so we would be cross-compiling
WebRTC for ARMv6. Jitsi's JVB is a heavy Java component aimed at browsers.

**Verdict: rejected** — worst fit for an ARMv6 endpoint, and the "browsers are
free" advantage is matched by Janus AudioBridge, which also mixes.

### 5. Custom Opus-over-UDP + our own Python mixer

My original proposal. Now strictly dominated: Janus AudioBridge delivers the
same topology (server-side mix, one stream down to the Pi) and the same codec
economics, while we write neither a jitter buffer, nor sequencing, nor loss
concealment, nor a browser bridge.

**Verdict: rejected, unless both Janus and Asterisk fail the spike.**

### 6. PulseAudio/PipeWire RTP modules, or Roc Toolkit

`module-rtp-send`/`module-rtp-recv` stream raw L16 with no real jitter buffer,
no auth and no mixing server. Roc Toolkit is a nicer version of the same idea
(FEC, latency-tuned) but is built for LAN point-to-point, not conferencing.

**Verdict: rejected** — too crude for a product, though `arecord | nc` style
plumbing is genuinely the right tool for the **spike's latency/CPU baseline**.

---

## Recommendation

**Janus Gateway + AudioBridge, Pi joins as a plain-RTP participant over
WireGuard, GStreamer pipeline on the device.**

Start at **8 kHz / PCMU** (zero codec cost on the Pi, lowest latency, and the
speakermic is a mono communications transducer that cannot resolve more anyway).
Keep **16 kHz / Opus** as a quality upgrade to try once it works, since it is a
one-line change in the room config and the join request.

This gives, concretely:

- exactly **one** inbound stream for the Pi to decode, forever, independent of
  how many boxes join;
- **no** Opus, DTLS, ICE or SRTP on the ARMv6 chip in the 8 kHz/PCMU
  configuration;
- no jitter buffer, resampler or mixer written by us;
- **browser clients in the same room for free**, which likely promotes Q43
  (browser VoIP) from v2 into v1;
- "except loopback" for free — an MCU never mixes a participant's own audio into
  their own output;
- nothing exposed: one WireGuard UDP port, Janus bound to the tunnel interface.

## Spike before committing (unchanged)

Still gate the decision on a measurement on the real board, now much cheaper to
run because there is no Python audio stack to build:

1. Janus in Docker with one AudioBridge room, `allow_rtp_participants = true`,
   8 kHz, over WireGuard.
2. Pi Zero W joins via the GStreamer pipelines above; measure **CPU** and
   **mouth-to-ear latency** (target < 150 ms, fail > 300 ms) with 2–3 simulated
   participants.
3. Then try a 16 kHz Opus room and compare both numbers.

Fallback order if it disappoints: **Asterisk ConfBridge + baresip**, then custom
UDP with our own mixer.

Keep the device daemon's audio module behind a narrow interface
(`start_tx()` / `stop_tx()` / `set_volume()` / state callbacks) so any of these
is a swap rather than a rewrite.

---

## Sources

- [Janus AudioBridge plugin documentation](https://janus.conf.meetecho.com/docs/audiobridge)
- [Add support for plain RTP participants in AudioBridge (janus-gateway PR #2464)](https://github.com/meetecho/janus-gateway/pull/2464)
- [Dial-out and cascaded mixing with the AudioBridge (Meetecho)](https://www.meetecho.com/blog/cascaded-mixing/)
- [Bridging AudioBridge and SIP with Drachtio (Meetecho)](https://www.meetecho.com/blog/audiobridge-sip/)
- [AudioBridge, RTP join, SIP scenarios (Janus forum)](https://janus.discourse.group/t/audiobridge-rtp-join-sip-scenarios/1049)
- [talKKonnect FAQ — Opus at 48 kHz vs single-core ARMv6 Pi Zero](https://www.talkkonnect.com/2020/04/21/talkkonnect-frequently-asked-questions/)
- [pymumble — Mumble client in Python (TCP tunnel only; UDP media listed as missing)](https://github.com/azlux/pymumble)
- [Mumble-Radio-Pi — Mumble PTT on Raspberry Pi](https://github.com/K6SM/Mumble-Radio-Pi)
