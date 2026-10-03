# Bipbox — Open questions before implementation

Status: **answered 2026-10-03** — superseded by [`architecture.md`](./architecture.md). Kept for the record.

Based on a full read of `context.md` and the `telex/` codebase (server: FastAPI +
SQLModel + SQLite; client: Python daemon + Flask portal; deploy: systemd +
golden-image scripts).

Each question has a **→ recommendation** so you can answer "defaults except Q7, Q12, …".

---

## Decisions taken (2026-10-02)

- **D1 (was Q1) — Stack:** FastAPI + SQLModel + SQLite on the server (port of
  telex), Flask for the Pi's local console. "Python/Flask" reads as "Python,
  Flask where it fits".
- **D2 (was F1) — PTT is on GPIO23.** The `GPIO3` references in `context.md`
  §Audio are a typo. Power button keeps GPIO3 (wake-from-halt preserved). GPIO23
  uses the Pi's internal pull-up for both the bare-switch and 2N7000 wirings.
  → `context.md` needs correcting.
- **D3 — Public IP must never appear in public DNS.** Audio endpoint details are
  pulled by the box from the bipbox server *after* authentication (over the
  Cloudflare-proxied HTTPS link), never published. Web clients stay entirely on
  the proxied HTTPS path.
- **D4 (was Q46) — Process:** one architecture document covering the whole
  system (architecture, data model, wire protocol, device/server API, pin map,
  LED timings) for review, then implementation feature-by-feature.
- **D5 — VoIP goals:** minimise latency; sound quality is explicitly *not* a
  priority (mono speakermic); respect the Pi Zero W's ARMv6 budget. Off-the-shelf
  software (e.g. Mumble) is acceptable rather than a hand-rolled protocol.

---

## Part 0 — Findings you should decide on first

These are contradictions or risks I found, not questions of taste.

### F1. GPIO3 is assigned twice (blocking)

`context.md` says:

- Power button: "directly between GPIO3 and ground" (§Pushbuttons)
- PTT: "to GPIO23" (§Pushbuttons)
- But then, in §Audio, **both** wiring variants route PTT to **GPIO3**
  ("Ring 2: To GPIO3" / "the drain goes to GPIO3")

GPIO3 is special: it is the only pin that wakes a Pi from a soft halt, so the
power button genuinely needs it. PTT cannot share it. I suspect "GPIO3" in the
audio section is a typo for "GPIO23", which matches the pushbutton section — but
the board is already made, so only you can confirm what is etched.

→ **Q: which is it on the actual PCB?** If PTT really is on GPIO3, we lose
power-on-from-halt (the box would only be power-cyclable from the C14 switch) or
we lose PTT — one of the two needs a wire mod.

Note on why GPIO2/GPIO3 are attractive for buttons: they carry the Pi's only
*external* 1.8 kΩ pull-ups to 3V3, so a bare switch-to-ground works with no
software pull-up. GPIO23 has no external pull-up, but the Pi's internal ~50 kΩ
programmable pull-up is fine for both a switch and the 2N7000's open drain.

**→ Answer:** PTT goes to GPIO23, it was a typo

### F2. 74HC595 at 5 V driven by 3.3 V logic is out of spec

The HAT powers the '595 from 5 V and drives SRCLK/SER/RCLK from the Pi (3.3 V).
A **74HC**595 at Vcc = 5 V needs V_IH ≥ 3.5 V (0.7 × Vcc). The Pi tops out at
3.3 V. It usually works at room temperature and then fails intermittently when
warm — exactly the kind of bug that is miserable to chase later.

Fixes, best first:

1. Swap the chip for a **74HCT595** (TTL input thresholds, V_IH = 2.0 V) —
   pin-compatible, no board change, keeps 5 V LED drive and your existing
   resistor values.
2. Power the '595 from 3V3 instead. But then the **blue LED won't work**: Vf ≈
   3.0–3.2 V, so (3.3 − 3.2)/180 Ω ≈ nothing. Not viable without changing
   resistors and probably the LED.

Current draw check, for the record (5 V rail, all four LEDs on): orange 180 Ω ≈
17 mA, blue 180 Ω ≈ 10 mA, two greens 300 Ω ≈ 10 mA each → ≈ 47 mA, inside the
'595's 70 mA package limit. So option 1 is clean.

→ **Q: do you want to swap to 74HCT595, or ship 74HC595 and accept the risk?**

**→ Answer:** I ordered some 74HCT595N and will try to unsolder the 74HC595N to swap it. Otherwise I will have to make new boards (I have spare PCBs).
In the meantime (the 74HCT595N won’t be here before a week) I will work with the 74HC595N (my guess is that it won’t change a thing software-wise).

### F3. No RTC on the BOM

Printed tickets show a timestamp. The Pi Zero W has no RTC, so after a power cut
without internet the clock is wrong until NTP succeeds.

→ **Recommendation: the printed timestamp comes from the server** (sent with the
message), not from the Pi. Pi-local time is only used for diagnostics. Cheap,
no hardware change.

**→ Answer:** Fine by me. Sending time makes more sense than printed time moreover.

### F4. Three bugs / design choices in telex not to carry over

1. `client/wifi_manager.py:__main__` reads `conf["uuid"]`, but `config.py`
   `DEFAULTS` has no `uuid` key → `KeyError` on every boot where no WiFi is
   found, i.e. the hotspot fallback is dead. (Bipbox needs a per-device UUID
   anyway — see Q14.)
2. `server/app/main.py` has a catch-all `@app.get("/{filename}")` returning
   `client.html`. That will collide head-on with bipbox's `/{channel}` and
   `/{channel}/{device}` routes and with `/admin`. Needs explicit routing.
3. The escalating fail2ban (`rate_limit.py`) is applied to the **device** API as
   well as the public one. A box with a stale password earns a *permanent* IP
   ban after 7 tries — which, since all your boxes may sit behind one home IP,
   can brick a sibling box too. Bipbox should use a separate, softer policy for
   devices (log + alert, never permanent) and keep the harsh policy for humans.

**→ Answer:** Agree to everything.

---

## Part 1 — Stack and architecture

### Q1. Flask or FastAPI for the server?

`context.md` says "Python/Flask as much as possible". But telex's server is
**FastAPI** (+SQLModel/SQLite) — only the Pi's local portal is Flask. Bipbox
also needs persistent bidirectional connections (telegraphy, VoIP, live LED
state), which is native in FastAPI/Starlette and needs `flask-socketio` +
eventlet/gevent in Flask.

→ **Recommendation: keep FastAPI on the server** (maximum reuse of telex, native
async WebSockets) and **keep Flask for the Pi's local console** (tiny, sync,
already written). "Python/Flask" then means "Python, Flask where it fits".

Confirm, or state that Flask is a hard requirement.

**→ Answer:** Agree with you: go for FastAPI.

### Q2. One server connection or three?

The LED spec talks about "the telex server", "the VoIP server" and "the
telegraphy server" as if they were distinct, and the local console mentions
"the remote server(s?)".

→ **Recommendation: one server, one address, one credential, one persistent
WebSocket** carrying three logical channels (telex / telegraphy / audio). The
three LEDs then reflect three *service states* over that one link. Much simpler
to configure and to secure.

Confirm, or tell me you want physically separate endpoints (e.g. audio on its
own host/port).

**→ Answer:** I agree. I talked about separate servers not to close any door (if using a dedicated VoIP server for instance) but of course a single endpoint is better.

### Q3. Push instead of poll — confirm

Telex polls every 60 s. Bipbox needs instant telegraphy and "blink fast when
receiving a telex", so the box must hold a persistent connection.

→ **Recommendation: persistent WebSocket with heartbeat; messages pushed
instantly; polling kept only as a reconnect/catch-up mechanism ("give me
everything I missed") at connect time**, which also satisfies "if we got
messages while the system was down, we print at startup".

**→ Answer:** Agree

### Q4. On-device process architecture

Three features all need the same scarce resources: the SPI bus / '595 (LEDs),
the GPIOs, the sound card, the printer. Running three independent systemd
services that each poke SPI will fight.

→ **Recommendation: one supervisor daemon** (`bipboxd`) owning GPIO, SPI/LEDs,
audio and printer, running telex/telegraphy/VoIP as asyncio tasks over the
single server connection; plus **a separate Flask process** for the local
console, talking to the daemon over a Unix socket. Two services instead of five,
and no resource contention.

Any objection? (The alternative — one service per feature + an "LED broker" —
is more moving parts for no gain at this scale.)

**→ Answer:** Agree. So SPIO and USB share the same bus ?

---

## Part 2 — VoIP (the biggest decision)

### Q5. Audio transport — revised after D3/D5

Your proposal (endpoint handed out post-authentication, never in DNS) is sound
and it unlocks **UDP**, which the Cloudflare proxy could never carry. That in
turn makes latency a solvable problem rather than a compromise. Two sub-questions
follow.

#### Q5a. Hide the audio port behind WireGuard?

Keeping the IP out of DNS stops casual discovery, but public IPv4 space is
scanned wholesale regardless of DNS, so an open audio port *will* be found and
probed. **WireGuard answers this properly: it does not reply to any packet that
fails key authentication, so the port is invisible to a scanner** — it looks like
a closed/filtered port.

Proposed shape:

- Each box holds a WireGuard peer config, **pulled from the bipbox server after
  HTTPS authentication** (exactly your mechanism, with the tunnel key instead of
  a bare IP).
- One UDP port open on your static IP. Nothing else. No DNS record.
- Audio binds to the tunnel interface only, so it is unreachable from the
  internet by construction.
- Boxes get stable private addresses — which also makes debugging and future
  SSH-in far nicer.
- Cost on a Pi Zero W: WireGuard is in-kernel on Bookworm (6.1), and a ~30 kbit/s
  stream is nowhere near its few-Mbit/s ceiling. Encryption overhead is
  negligible at this rate.
- Web clients do **not** get the tunnel; they stay on the Cloudflare-proxied
  HTTPS path.

→ **Recommendation: yes, WireGuard.** It costs one `wg0` interface and gives you
a genuinely unprobeable attack surface plus per-device key revocation.

Objection to consider: it adds a provisioning step and a failure mode ("tunnel
down" is now a distinct state the VoIP LED must express).

**→ Answer:** I am not against, even if it complicates things a bit… and wireguard adds overhead to the Pi.
We should also consider using a non-standard port (even if it has very low security impact) and more importantly port-knocking (or even dynamically opening the port just for the client IPs through my firewall). My firewall is on a powerful OpenWRT VM and I can consider running a service to dynamically open the connections for the client addresses.

#### Q5b. Which audio stack? — SUPERSEDED by `voip-options.md`

**The Mumble recommendation below is withdrawn.** A proper survey of the
alternatives (see **[`voip-options.md`](./voip-options.md)**) reaches a different
conclusion: **Janus Gateway's AudioBridge plugin**, with the Pi joining as a
*plain RTP participant* over WireGuard and a GStreamer pipeline on the device.

In one line: AudioBridge **mixes server-side**, so the Pi decodes exactly one
stream forever instead of one per speaker, and it accepts **G.711/PCMU at
8 kHz** from plain-RTP participants, so the ARMv6 chip does no Opus, DTLS, ICE or
SRTP work at all — while browser clients join the very same room over native
WebRTC for free.

Mumble is rejected on measurements, not taste: its protocol is 48 kHz Opus only
and it *forwards* rather than mixes, and the talKKonnect project (Mumble PTT
radios on Raspberry Pis) reports 48 kHz Opus at **nearly 100% CPU on a
single-core ARMv6 Pi Zero**. `pymumble` also has no UDP media path at all —
TCP tunnel only, which contradicts D5.

The original Mumble analysis is kept below for the record.

<details>
<summary>Withdrawn: original Mumble-vs-custom analysis</summary>


**Mumble (`mumble-server`/murmur + `pymumble` on the Pi) — recommended**

What you get for free, all of it stuff I would otherwise be writing badly:

- A tuned **adaptive jitter buffer**. This is the single most underestimated
  piece of any voice system, and the main reason hand-rolled audio sounds worse
  than it should.
- Opus, 10/20 ms frames, genuinely low latency — Mumble's whole reputation is
  built on this.
- A native **channel** concept with ACLs that maps 1:1 onto your bipbox
  channels, plus per-user certificates, encryption, and no self-echo
  ("except loopback" comes free — Mumble never sends you your own audio).
- `mumble-web` + `mumble-web-proxy` give browser clients a WebRTC path later,
  which is most of Q43's cost removed.
- Mature, packaged, and someone else maintains the audio stack.

Costs and risks, honestly:

- **The Pi-side risk is `pymumble` on ARMv6, and it must be spiked early.**
  Mumble's protocol is 48 kHz-only, so we cannot drop to 16 kHz to save CPU, and
  Mumble does **not** mix server-side — the client decodes one Opus stream *per
  speaker*. With 2–3 boxes that is 2–3 decodes plus one encode in CPython on a
  single 1 GHz ARMv6 core. I believe it fits (`botamusique` runs on small Pis)
  but I will not claim it without measuring.
- A second identity system to provision (Mumble certs/passwords) alongside
  bipbox's. Manageable: the bipbox server creates the Mumble channel and issues
  the device's credentials, and the box pulls them with everything else.
- `pymumble` and `mumble-web` are community-maintained, not bulletproof.

**Custom UDP + server-side mixing (the fallback)**

- 16 kHz mono Opus, 20 ms frames, our own 6-byte header over the WireGuard
  tunnel; the server mixes per channel and sends each box exactly **one** stream
  to decode.
- Lowest possible CPU on the Pi (1 decode, 1 encode), lowest bandwidth, lowest
  latency, no second auth system.
- But I write the jitter buffer, packet loss handling and sequencing — and that
  is precisely where a naive implementation turns into choppy audio.

→ **Recommendation: design for Mumble, but gate it on a hardware spike** (phase
0: `pymumble` + `opuslib` on a real Pi Zero W, measure CPU and mouth-to-ear
latency with 3 simulated speakers). Keep the daemon's audio module behind a
narrow interface so the fallback is a swap, not a rewrite. If the spike shows
CPU headroom is thin, custom UDP with server-side mixing wins on the merits.

Do you agree with spiking first, or would you rather commit to one now?

</details>

→ **Recommendation: Janus AudioBridge, still gated on a hardware spike** (now
much cheaper to run: Janus in Docker + two GStreamer pipelines, no Python audio
stack to build). Measure CPU and mouth-to-ear latency on the real board at
8 kHz/PCMU, then try 16 kHz/Opus. Fallback order: Asterisk ConfBridge + baresip,
then a custom mixer. Full reasoning and sources in
[`voip-options.md`](./voip-options.md).

**→ Answer:** Agreed for Janus

#### Q5c. Room-per-channel mapping

One bipbox channel → one Janus AudioBridge room; each device → one plain-RTP
participant; each web client → one WebRTC participant in the same room. Our
server creates and configures rooms through Janus's API; the bipbox admin console
stays the only UI you touch, and Janus's own admin surface is never exposed.

→ **Recommendation: yes.** Rooms created on demand from the channel definition,
so adding a channel in the admin console needs no Janus configuration by hand.

**→ Answer:** Agree

### Q6. Half-duplex or full-duplex?

A shoulder speakermic with PTT is classically half-duplex, and the box's speaker
sits ~10 cm from its own mic — full duplex will howl without acoustic echo
cancellation (which we do not want to write for an ARMv6 chip).

→ **Recommendation: half-duplex. While PTT is held: mic streams, local speaker
is muted.** Confirm.

**→ Answer:** Half-duplex of course

### Q7. Contention: two people press PTT at once

→ **Recommendation: server mixes both** (simplest, and "everyone hears
everyone"). Alternative is first-come-wins with a "busy" indication (more
walkie-talkie-authentic, more code, and a child holding PTT could lock the
channel).

Which do you want?

**→ Answer:** server mixes both

### Q8. Does audio need to be recorded or logged?

→ **Recommendation: never recorded; only "who talked when" metadata, if
anything.** Confirm — it changes the privacy story and the admin UI.

**→ Answer:** No record. Metadata could be logged if the feature is easy to implement and should be controlled by a on/off switch in the admin console (disabled by default)

### Q9. The "bip" (telegraphy tone)

Spec says it plays as long as someone else holds their button, with
frequency/volume tunable locally.

→ **Recommendation: generated locally on the Pi** (no audio sent over the wire
for telegraphy — just a press/release event), mixed with VoIP playback via ALSA
`dmix` (the UGREEN card won't mix in hardware). Confirm, and tell me defaults
you'd like (I'd suggest 880 Hz, soft attack/release to avoid clicks).

**→ Answer:** This is exactly what I had in mind.

### Q10. Target audio latency — answered by D5, one number to confirm

Quality is settled (voice-radio is fine). Latency is the target to design
against, and it is what the Q5b spike must measure.

→ **Recommendation: aim for < 150 ms mouth-to-ear, treat > 300 ms as a failure.**
Over WireGuard/UDP to your own static IP this is realistic; it was not with
WebSockets through Cloudflare. Frame size: 20 ms (10 ms shaves latency but
costs proportionally more CPU and packet overhead on the Pi — a spike question).

Sample rate is forced to 48 kHz if we take Mumble; 16 kHz if we go custom.

**→ Answer:** Preferably < 150ms, <500ms worst case scenario (this is just a toy after all)

---

## Part 3 — Channels, devices, accounts

### Q11. Can a device belong to more than one channel?

`context.md`: "It must be part of a channel" (singular).

→ **Recommendation: exactly one channel per device** (simple mental model, and
it makes "who do I broadcast to" unambiguous). Confirm.

**→ Answer:** Yes only one channel

### Q12. Can a web client belong to more than one channel?

Same question for human accounts — e.g. a grandparent who wants to reach two
different channels.

→ **Recommendation: allow multiple channel memberships per web account**, each
membership carrying its own permissions (which devices it may reach).

**→ Answer:** If it is easy, yes, but real use cases seems very unlikely IMO

### Q13. Does telex's "simple send password" model survive?

Telex let *anyone* with a URL and an easy password (a date of birth) send a note,
with no account. Bipbox's spec replaces this with proper accounts (client ID +
strong password) at `/{channel}` and `/{channel}/{device}`.

→ **Recommendation: drop anonymous sending entirely.** It is the right call for
a channel children listen to. Confirm — it means family members each get an
account, which is more friction for them. (Middle ground available: keep a
per-device "guest link" that an admin can enable/disable and that is rate
limited hard.)

**→ Answer:** I confirm, drop it

### Q14. Device identity and authentication

Spec floats "a long ID and key couple or even mTLS".

→ **Recommendation: UUID + 32+ byte secret, bcrypt-hashed server side** (like
telex, which already works), entered once in the local console. **mTLS is a trap
here**: the Cloudflare proxy terminates TLS, so client certificates never reach
your origin unless you use Cloudflare's mTLS/Access features — extra cost and
complexity for no real gain over a long secret on an HTTPS link.

Confirm, or tell me mTLS is a hard requirement (then we need a non-proxied
hostname for device traffic).

**→ Answer:** Yes I think the long password is enough

### Q15. Admin console authentication

Telex has a single `ADMIN_API_KEY` sent in a header — not enough for what you
describe ("strong password + fail2ban + TOTP").

→ **Recommendation: single admin account, Argon2id password + TOTP (pyotp),
server-side sessions in secure cookies, the existing escalating fail2ban applied
to `/admin`, `/admin` also behind a Cloudflare Access rule if you want
belt-and-braces.** One admin user or several?

**→ Answer:** Yes for the recommendation. A single admin user.

### Q16. Can a device be moved/renamed without re-provisioning?

→ **Recommendation: yes — identity is the UUID, the alias and channel are server
side and can change at any time.** Confirm.

**→ Answer:** Yes

---

## Part 4 — Telex (printing)

### Q17. Paper width per box

Telex hardcodes `PAPER_WIDTH = 42` (80 mm). You list a PRP-250 **and** a
TM-T20II. If one of them is 58 mm, the layout and image width (384 vs 576 dots)
differ.

→ **Q: which printer in which box, and what paper width each?**
→ **Recommendation: make width a per-device setting** (auto-detected where
possible, overridable in the local console), not a constant.

**→ Answer:** Both my printers are 80mm. However, having the width as a setting is a good idea.
Keep in mind that the interface should reflect the final rendering of the printed ticket. If recipients have different paper size, at least a warning should be displayed.

### Q18. Drawings and photos

Telex already has a canvas drawing tool (fill, grayscale palette, undo, S/M/L
sizes) and photo upload, printed via PIL `convert("1")`.

→ **Recommendation: reuse as-is, but replace the 1-bit threshold with
Floyd–Steinberg dithering** — plain `convert("1")` destroys photos. Also cap
image dimensions server-side to the device's dot width.

Anything you disliked about telex's drawing UI that I should change?

**→ Answer:** I did not check it for a while but I believe it was very good.
The 1 bit threshold was to make the picture look as good as possible despite the ESC/POS limitations. If Floyd–Steinberg dithering can make better results then let’s go.
When you say cap dimensions server-side: do you mean resize server-side ? If so yes. If you mean limit what a user can upload then yes BUT with a comfortable value: a user should not have to resize regular pictures himself before uploading (of course, not talking about a 100MP tiff picture).

### Q19. Print queue durability

Spec: retry every 30 s; print what was missed at startup.

→ **Recommendation: the *server* is the queue** (telex's `Delivery` table
already models `pending → delivered → printed | failed`). The Pi keeps only a
small local spool for messages received-but-not-yet-printed, so a paper jam
doesn't lose anything and a reboot replays. Confirm.

**→ Answer:** Confirmed.

### Q20. Message retention

How long should messages (and their images) be kept on the server? Images are
base64 in SQLite today, which will bloat.

→ **Recommendation: images on disk (or object storage) with a DB reference;
retention 1 year, admin-configurable, with a purge job.**

**→ Answer:** Perfect

### Q21. Max message length

Telex: 500 chars, 600 kB image. Keep?

**→ Answer:** for the image, if you are talking about what is sent to the client, then yes.

---

## Part 5 — Local management console & networking

### Q22. Does the local console need a password?

Telex's portal is **wide open on port 80** — anyone on the home WiFi can read
and rewrite the server credentials. For bipbox it also exposes audio settings
and the printer.

→ **Recommendation: a local admin password, set at first boot (printed on the
setup ticket), required for anything that writes.** Read-only status page can
stay open. In AP-mode setup it has to be open by nature (first-run only).
Confirm.

**→ Answer:** Confirmed

### Q23. AP-mode SSID collision

Spec says the AP is called "bipbox". With two boxes in the same house in
fallback mode, two identical SSIDs.

→ **Recommendation: SSID `bipbox-XXXX`** (last 4 of the UUID or the MAC), shown
on the printed setup ticket along with the AP password and a QR code (telex
already prints a QR). Confirm.

**→ Answer:** Confirmed

### Q24. mDNS hostname collision

Same issue for `bipbox.local`.

→ **Recommendation: default hostname `bipbox-XXXX`, renameable from the console**
(spec already wants the hostname tunable). Or do you want literally
`bipbox.local` by default and accept that two boxes on one LAN need a manual
rename?

**→ Answer:** Confirmed

### Q25. WiFi credentials in a file on the SD card

Spec: "ideally we should be able to store Wi-Fi creds in a text file on the SD
card".

→ **Recommendation: a plain `bipbox.conf` (or `bipbox-wifi.txt`) on the FAT32
boot partition**, readable/writable from any Windows/Mac/Linux machine without
mounting ext4. Read at boot, merged into NetworkManager, then (optionally)
blanked. Should it also be able to carry the **server URL + device credentials**,
so a box can be fully provisioned by editing one file on the card? (I'd say
yes — it makes the "fire it up with no keyboard" goal real.)

**→ Answer:** Perfect

### Q26. Keep NetworkManager/nmcli?

Telex uses `nmcli` (Bookworm default).

→ **Recommendation: yes, keep it.** Confirm. Do you also need static IP /
DHCP-reservation support in the console (spec says "maybe")? I'd defer it.

**→ Answer:** I do not care, choose the best solution. We can rely only on DHCP.

### Q27. Will boxes ever be on the same LAN?

If the two boxes are at different homes (son / grandson), everything goes through
the server. If they can be on the same LAN, a direct peer mode would cut latency
and survive an internet outage.

→ **Recommendation: assume always via the server** (no LAN peer mode) unless you
tell me otherwise.

**→ Answer:** Yes, even if on the same LAN still go through the server.

---

## Part 6 — LEDs, buttons, boot

### Q28. Pi activity LED on GPIO26

→ **Recommendation: do it in firmware, not software** — `dtparam=act_led_gpio=26`
in `config.txt` makes the kernel drive GPIO26 exactly like the onboard ACT LED,
for free and with no daemon. Confirm (note: it then reflects SD-card activity,
which is what "like its embedded LED" means — correct, but it is *not* a
"system healthy" indicator).

**→ Answer:** Yes of course

### Q29. Power button behaviour

→ **Recommendation: `dtoverlay=gpio-shutdown,gpio_pin=3` for the soft shutdown;
wake-from-halt is a firmware property of GPIO3 and needs no software.** Should a
long press force a hard power-off, or is a clean shutdown always enough?

**→ Answer:** Yes of course. For the long press, not necessary: we have a power switch by the power socket

### Q30. LED blink rates — define them now

Spec says "slow", "fast", "quick blink every 2 s", and a `. . _` pattern for AP
mode. I need numbers.

→ **Recommendation:** slow = 1 Hz (500 ms on / 500 ms off); fast = 5 Hz
(100/100); activity flash = 50 ms per event, minimum 150 ms visible; "quick
blink every 2 s" = 80 ms on, 1920 ms off; AP pattern = 150, 150, 450 ms with
150 ms gaps then a 700 ms pause, repeating.

Adjust anything that will look wrong to you.

**→ Answer:** Perfect for everything except the AP pattern: remote the 700ms pause.

### Q31. LED priority

The telex LED has to show both "connected" (steady) and "receiving" (fast
blink); VoIP shows "connected" and "sending/receiving". So activity must
temporarily override state.

→ **Recommendation: activity wins for as long as it lasts, then the LED falls
back to state.** Confirm.

**→ Answer:** Confirmed

### Q32. Should a telegraphy press be visible anywhere else?

E.g. logged on the server, shown in the web client, or counted.

→ **Recommendation: not persisted (realtime only), but the web client shows the
live light.** Confirm. And: if the other box is **offline**, does the sender get
any feedback that nobody is listening? (I'd suggest the web UI shows presence;
the box itself does not — too subtle for a child.)

**→ Answer:** Confirmed (and I thought it was clear in the specs for the web-client)

---

## Part 7 — Image, deployment, repo

### Q33. OS version

Pi Zero W is ARMv6 → **32-bit Raspberry Pi OS Lite only** (armhf; arm64 won't
boot, and many aarch64-only wheels are irrelevant).

→ **Q: Bookworm (stable, what telex targets) or Trixie?**
→ **Recommendation: Bookworm Lite armhf** — piwheels coverage and NetworkManager
behaviour are known-good there.

**→ Answer:** Confirmed

### Q34. How is the distributable image built?

Telex's method is "set a Pi up by hand, `prepare-image.sh`, then `dd` the card"
— a golden image. It works but is not reproducible and bakes in whatever state
that one Pi had.

→ **Recommendation: `pi-gen` (or CustomPiOS) in a Docker build**, producing a
versioned `.img.xz` from source. Slower to set up, then one command forever
after, and diffable.

Golden-image or pi-gen?

**→ Answer:** pi-gen obviously (plus I had been dying to play with it ;p)

### Q35. First-boot provisioning

Each flashed card must end up with a unique UUID, hostname and AP SSID, and keys
that are not shared between boxes.

→ **Recommendation: a `bipbox-firstboot.service` oneshot** that generates the
UUID, sets the hostname, regenerates SSH host keys, expands the filesystem,
applies `bipbox.conf` from the boot partition if present, and prints the setup
ticket. Confirm.

**→ Answer:** Confirmed

### Q36. Auto-update

Telex pulls the latest GitHub release every 24 h and restarts services.

→ **Q: keep it? Will the bipbox repo be public or private?** (Private needs a
deploy token on each box — a secret on an SD card in a child's room. I'd lean
public repo, or no auto-update and a "check for update" button in the local
console.)

**→ Answer:** The repo will be public. But I still prefer a check for update button in the console to auto-update (too « dangerous »)

### Q37. Repo layout

`/Users/guillaume/Workspace/bipbox` is **not currently a git repo**, and `telex/`
is a nested repo with its own history.

→ **Q: what do you want?**
→ **Recommendation:** `git init` bipbox as a fresh repo; keep `telex/` for
reference during the port but **git-ignore it** (so we never commit someone
else's history), then delete it once the port is done. Port code by hand with
attribution in commit messages rather than copying wholesale.

**→ Answer:** Confirmed

### Q38. CAD files in git?

`CAD/` + `.CAD_orig/` hold FreeCAD `.FCStd`, `.FCBak` and KiCad files — tens of
MB of binaries that change often.

→ **Recommendation: commit KiCad sources + exported PDFs/STEP; `.gitignore` all
`.FCBak`; use git-lfs for `.FCStd`/`.step`, or keep CAD in its own repo.** Also:
what is the difference between `CAD/` and `.CAD_orig/` — is one of them dead?

**→ Answer:** Follow your reco. I’d even add that I will myself commit the CAD files in a second time. `.CAD_orig` is dead, I keep it only for reference.

### Q39. Target directory layout

→ **Recommendation:**

```
bipbox/
├── server/            # FastAPI + docker-compose (port of telex/server)
├── device/            # bipboxd daemon + Flask local console
├── image/             # pi-gen config, first-boot, systemd units
├── docs/              # specs, this file, hardware notes
└── CAD/               # KiCad + FreeCAD
```

Objections?

**→ Answer:** Perfect

---

## Part 8 — Product, scope, process

### Q40. How many boxes, and what hardware exists right now?

- How many boxes will exist (2 — son and grandson — or more later)?
- Are the HATs built? How many?
- Do you have a Pi Zero W flashed and reachable *today* for testing?
- Which speakermic model(s), and therefore which of the two PTT wirings is
  actually populated?

This decides whether I can test on hardware as we go or must write against a
simulator first. (I'd build a `--fake-hardware` mode either way, so the daemon
runs on your Mac.)

**→ Answer:** I built 2 boxes (one for my son and one for my **god**son, I must have made a typo earlier). I will maybe build more one day. And I want to publish the source code and schematics so people can make their own and they might make any number.
I built 2 hats (but I need to change the 74HC595 as soon as I get the 74HCT595).
I have two Pi Zero W. One was flashed and used for telex testing. Both can be used for testing.
I have 2 speakermic and they are different. One is a baofeng "for Baofeng BF-T1 BF-T8 BF-U9 UV-3R Plus » and the other one is a no-name "For YAESU VERTEX VX-3R FT-60R FT1DR FT2DR VX-10 VX-17 VX-110 VX-150 VX-130 » each has a different wiring (first one with a dedicated PTT wire, the other share the mic’s signal with PTT) and thus I made two versions of the PCBs when I soldered the components.

### Q41. UI language and look

telex's UI is French, its code and comments are English, and it has an
amber/green phosphor terminal theme. `logo.png` / `Logo.svg` exist for bipbox.

→ **Recommendation: French UI, English code/comments, reuse the terminal theme
with the bipbox logo, no i18n framework.** Confirm.

**→ Answer:** English code/comments for sure. The UI must be available in French but I would also like to have it in English for open-source sharing reasons. We can start with the amber/green terminal theme but I will probably try something more suited to the new bipbox logo.

### Q42. Who are the web clients, realistically?

Grandparents on phones? How many accounts? Does the web client need to be a PWA
/ installable (telex has a manifest)? Does it need to work on an old iPhone?
(iOS Safari is the thing most likely to break browser-side mic capture.)

**→ Answer:** Parents, grandparents, maybe friends. A PWA would be great but not the highest priority. We can take the decision right now to design it only for modern and up-to-date devices.

### Q43. Is VoIP-from-browser in scope for v1?

Spec hedges: "and, if possible, join the VoIP and telegraphy channel".

→ **Recommendation revised: put it in v1.** This was the most expensive item
only under the Mumble/custom-protocol designs. With Janus AudioBridge (see
`voip-options.md`) browsers are *native* WebRTC participants in the same room
the boxes join — there is no bridge to write and no second server. The remaining
work is UI plus the iOS-Safari mic-permission dance from Q42.

**→ Answer:** Let’s go then

### Q44. Email alerts

Telex emails the admin on permanent bans and client locks.

→ **Recommendation: keep, and add "device offline > N minutes".** Do you want
anything else alerted? Would you prefer ntfy/Telegram over SMTP?

**→ Answer:** Both SMTP and ntfy (I already have a server).

### Q45. Proposed phasing — agree or reorder?

1. **Foundations** — repo, server skeleton (FastAPI, channels/devices/accounts,
   admin auth with TOTP), device daemon skeleton with LED/button/SPI support and
   a fake-hardware mode.
2. **Telex** — port from telex: printer detection, ESC/POS layout, drawing/photo
   with dithering, queue + receipts, web client send page.
3. **Telegraphy** — WS event channel, LED, local bip, web button.
4. **Local console + WiFi/AP** — port and extend the Flask portal, boot-partition
   config file.
5. **VoIP** — Opus over WS, server mixer, half-duplex PTT.
6. **Image** — pi-gen, first-boot, docs for flashing.
7. **(v2)** Browser VoIP.

**→ Answer:** Perfect
### Q46. How do you want to work?

Do you want me to write a full spec document (architecture + data model + wire
protocol + API) for review *before* any code, or go feature-by-feature with a
short spec then an implementation for each? (I'd suggest: one architecture doc
covering the whole system, then feature-by-feature.)

**→ Answer:** Agree, I follow your reco