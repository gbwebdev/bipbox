# Bipbox — Architecture

Version 0.1 — 2026-10-03 — **for review before implementation**

Derived from [`../context.md`](../context.md), the 46 answers in
[`open-questions.md`](./open-questions.md) and the transport survey in
[`voip-options.md`](./voip-options.md). Where this document and `context.md`
disagree, this document wins and `context.md` should be corrected.

Open decisions still needing your input are marked **[OPEN]**. There are three.

---

## 1. System overview

```
   ┌─ Browser (parent/grandparent) ──────────────────────────────────────┐
   │  HTTPS + WSS  ──────────────► Cloudflare proxy ──► bipbox server    │
   │  WebRTC media (UDP) ─────────────────────────────► Janus  [OPEN-1]  │
   └─────────────────────────────────────────────────────────────────────┘

   ┌─ Bipbox (Pi Zero W) ────────────────────────────────────────────────┐
   │  HTTPS + WSS  ──────────────► Cloudflare proxy ──► bipbox server    │
   │  plain RTP (G.711) ──► WireGuard tunnel ────────► Janus AudioBridge │
   └─────────────────────────────────────────────────────────────────────┘

                    ┌──────────── your host ─────────────┐
                    │  bipbox-server  (FastAPI)          │
                    │  janus          (AudioBridge MCU)  │
                    │  wireguard      (hub)              │
                    │  SQLite + image store (volumes)    │
                    └────────────────────────────────────┘
```

Two planes, deliberately separated:

- **Control plane** — everything except voice. HTTPS/WSS through the Cloudflare
  proxy. Telex messages, telegraphy events, presence, config, admin, LED state.
  No inbound ports on your IP; Cloudflare is the only front door.
- **Media plane** — voice only. UDP. Never through Cloudflare (which cannot
  carry it). Devices reach it inside WireGuard; browsers reach it directly
  (**[OPEN-1]**).

Because the media plane is plain RTP — no DTLS, no SRTP, no ICE on the Pi — its
confidentiality and authentication come **entirely** from the tunnel. That is
the trade that lets an ARMv6 chip do zero codec and zero crypto work.

---

## 2. Components

| Component | Tech | Where |
|---|---|---|
| `bipbox-server` | Python 3.11, FastAPI, SQLModel, SQLite | Docker, behind Cloudflare |
| `janus` | Janus Gateway + `audiobridge` plugin | Docker, same host |
| `wireguard` | kernel WireGuard | the host, or your OpenWRT VM (**[OPEN-2]**) |
| `bipboxd` | Python 3.11, asyncio | Pi Zero W, systemd |
| local console | Python 3.11, Flask | Pi Zero W, systemd, port 80 |
| audio pipeline | GStreamer 1.0 (Debian armhf packages) | Pi Zero W, inside `bipboxd` |

### 2.1 Why two processes on the device, not five

`bipboxd` owns every shared resource: the SPI bus and the '595, the GPIO lines,
the ALSA device, the printer, and the single WebSocket to the server. Telex,
telegraphy and VoIP are asyncio tasks inside it, not separate services. The
local console is a separate Flask process (sync, trivially simple, must survive
`bipboxd` crashing) and talks to the daemon over a Unix socket at
`/run/bipbox/ctl.sock`.

Note for the record, since it came up: **SPI and USB share nothing.** SPI0 is a
dedicated peripheral; USB is the OTG port feeding the hub. The sound card and
printer do share that one USB port, which is 480 Mbit/s against a 64 kbit/s
audio stream — irrelevant.

---

## 3. Hardware interface

### 3.1 Pin map (authoritative)

| Function | Pin | Direction | Notes |
|---|---|---|---|
| Power button | GPIO3 | in | `dtoverlay=gpio-shutdown`; wake-from-halt is firmware. No software. |
| Telegraphy button | GPIO2 | in | Active low. External 1.8 kΩ pull-up + RC debounce on the HAT. |
| PTT | GPIO23 | in | Active low. **Internal** pull-up enabled in software. |
| Pi activity LED | GPIO26 | — | `dtparam=act_led_gpio=26`. Kernel-driven, no software. |
| '595 SER | GPIO10 / SPI0 MOSI | out | |
| '595 SRCLK | GPIO11 / SPI0 SCLK | out | |
| '595 RCLK | GPIO7 / SPI0 CE1 | out | `/dev/spidev0.1` |

`config.txt` additions: `dtparam=spi=on`, `dtparam=act_led_gpio=26`,
`dtoverlay=gpio-shutdown,gpio_pin=3`.

### 3.2 Driving the 74HC(T)595 over hardware SPI

Using CE1 as the latch is neat and it works: spidev holds CS low for the
transfer and releases it high afterwards, and that **rising edge is exactly what
RCLK needs**. SPI mode 0 (CPOL=0/CPHA=0) shifts data out on the falling clock
edge while the '595 samples on the rising edge — compatible. One byte per
update, written only when the byte changes.

Output mapping, shifting MSB-first (so the first bit sent lands on QH):

| Bit | '595 output | LED |
|---|---|---|
| 5 | QF | WiFi (blue, 180 Ω) |
| 4 | QE | VoIP (green, 300 Ω) |
| 3 | QD | Telex (green, 300 Ω) |
| 2 | QC | Telegraphy (orange, 180 Ω) |
| 0,1,6,7 | QA,QB,QG,QH | unused, held 0 |

**To verify on first light-up**, since bit order is the classic place to get this
wrong: write `0b00000100` and confirm only the orange LED lights. The wiring
tool (§3.5) does this as a guided bit-walk and writes out the discovered map.

On the chip: you're swapping to **74HCT595N** (F2). Software is unaffected —
the fix is purely about input thresholds, so develop against the 74HC595N now
and nothing changes when you re-solder.

### 3.3 PTT wiring variants

Two boxes, two speakermics, two PCB build variants:

| Box | Speakermic | HAT variant | PTT electrical path |
|---|---|---|---|
| A | Baofeng BF-T1/T8/U9/UV-3R+ | Wiring 1 | Dedicated ring → switch to GND |
| B | Yaesu/Vertex VX-3R, FT-60R… | Wiring 2 | Mic bias → 10 kΩ → 2N7000 gate → drain |

Both present as **active-low on GPIO23**, so the software path is likely
identical: internal pull-up, falling edge = pressed. Wiring 2 has an RC on the
gate (10 kΩ + 100 nF ≈ 1 ms) so its edges are slower, and the MOSFET may chatter
at the thresholds. The config therefore carries
`ptt_wiring: dedicated | shared` and a per-variant debounce value (default 20 ms
dedicated, 50 ms shared), rather than assuming they're interchangeable.

### 3.4 LED state machine

One `LedController` task ticking at 50 Hz (20 ms), ample for the fastest 80 ms
pattern. Each LED resolves to a pattern from (service state) with (activity)
overriding it while active, per Q31.

Timings (Q30, with the AP pause removed as you asked):

| Pattern | Definition |
|---|---|
| `off` / `on` | steady |
| `slow` | 500 ms on / 500 ms off |
| `fast` | 100 ms on / 100 ms off |
| `heartbeat` | 80 ms on / 1920 ms off |
| `ap` | 150 on, 150 off, 150 on, 150 off, 450 on, 150 off — repeating, no pause |
| `flash` | 50 ms per event, held visible ≥ 150 ms |

| LED | off | slow | fast | on | heartbeat |
|---|---|---|---|---|---|
| **WiFi** (blue) | down | searching | connecting | connected | — |
| | | | | *`ap` pattern when in AP mode* | |
| **Telex** (green) | client down | connecting | *activity:* receiving/printing | connected | — |
| **VoIP** (green) | client down | connecting *or tunnel down* | *activity:* tx/rx audio | connected | — |
| **Telegraphy** (orange) | connected, idle | connecting | — | **someone else is pressing** | client down |

Note the telegraphy LED is inverted relative to the others: its *steady on* is
the event, and "all well" is off. That is per spec and intentional.

### 3.5 `wiring_test.py` — standalone hardware bring-up tool

`device/tools/wiring_test.py`: one file, no project dependencies, runnable as
`sudo python3 wiring_test.py` on a bare Raspberry Pi OS Lite install. It exists
so the HAT can be brought up and iterated on **before any of bipbox exists**,
and it stays afterwards as a field diagnostic.

It deliberately avoids `python-escpos`, `pyusb` and anything from `bipboxd`:
printing is raw ESC/POS bytes written to `/dev/usb/lp0` or `/dev/ttyACM*`, audio
is `aplay`/`arecord`, so the only requirement is
`apt install python3-spidev python3-gpiozero`.

What it does:

- **LED bit-walk** — lights each '595 output in turn, asks which LED lit, and
  writes the discovered bit map to `wiring_test.json`. This converts §3.2's bit
  order from an assumption into a measured fact.
- **LED by name**, and a **preview of every blink pattern** from §3.4, so the
  timings can be judged on real LEDs rather than on paper.
- **Button monitor** — live state of GPIO2 and GPIO23 with transition counts and
  **measured bounce duration**. This is what settles whether the two PTT wiring
  variants (§3.3) need different debounce values, instead of guessing.
- **Audio** — tone to the speaker, then record-and-play-back from the mic, with
  a level meter. Exercises the TRRS wiring and the UGREEN card. **Starts at 40%
  and never jumps to full scale**, to protect the speaker.
- **Volume sweep** — steps the hardware mixer upward from 10%, stopping the
  moment it is told the tone is too loud, distorted or buzzing, and writes the
  last good level to `wiring_test.json` as the proposed `volume_max_pct`
  (§6.3.1). Prompts for confirmation before every step above 70%.
- **Printer** — detect and print a test ticket.
- **`--selftest`** — runs everything in sequence with a PASS/FAIL summary.

---

## 4. Data model

SQLModel, SQLite. Ported from telex where it already fits.

```python
Channel(id, slug UNIQUE, name, janus_room, created_at)

Device(uuid PK, channel_id FK, alias, secret_hash,
       paper_cols=42, paper_dots=576, printer_info, ptt_wiring,
       wg_pubkey, wg_ip, fw_version,
       last_seen, ip_address, mac_address, enabled, created_at)

Account(id, login UNIQUE, display_name, password_hash,
        totp_secret, is_admin, enabled, created_at, last_login)

Membership(id, account_id FK, channel_id FK,
           can_telex, can_voip, can_telegraphy, all_devices)
MembershipDevice(membership_id FK, device_uuid FK)      # when all_devices=False

Message(id, channel_id FK, author_account_id, author_device_uuid,
        text, image_path, image_mode, created_at)
Delivery(id, message_id FK, device_uuid FK, status,
         created_at, delivered_at, printed_at, error_msg)

FailedAttempt(id, ip, subject, scope, attempted_at)     # scope: human | device
IPBan(id, ip, subject, scope, level, banned_at, banned_until, active, lifted_at)

Setting(key PK, value)                                  # admin-tunable runtime config
VoipEvent(id, channel_id, participant, started_at, ended_at)   # only if enabled

WgPeer(id, kind, device_uuid, account_id, label,        # kind: device | browser
       pubkey UNIQUE, wg_ip UNIQUE, created_at, last_handshake, enabled)

Webhook(id, event, url_template, method, headers_json, auth_kind, auth_secret,
        allow_insecure_tls, body_template, timeout_s, retries, enabled)
```

Decisions baked in:

- **One channel per device** (Q11): `Device.channel_id`, not a join table.
- **Many channels per account** (Q12): `Membership` is a join table, because it
  costs nothing now and retrofitting it later costs a migration. The admin UI
  exposes a single channel per account until you ask for more.
- **Images on disk** (Q20): `Message.image_path`, not base64 in SQLite.
  `image_mode` records `dither` or `threshold` so a reprint reproduces exactly
  what was printed.
- **`Setting`** holds retention days, VoIP-metadata logging (off by default,
  Q8), alert thresholds, bip defaults.
- **`FailedAttempt.scope`** separates human from device bans (F4.3) so a box
  with a stale secret can never permanently ban your home IP, and with it the
  sibling box.

### 4.1 Password hashing — deliberately two algorithms

| Secret | Algorithm | Why |
|---|---|---|
| `Account.password_hash` | **Argon2id** | Human-chosen, low entropy, needs to be slow. |
| `Device.secret_hash` | **HMAC-SHA256** with a server-side pepper | 32 bytes from `secrets.token_urlsafe` — ~190 bits. Brute force is not a threat model, and this is verified on *every* reconnect, so paying bcrypt there is waste, not safety. |

### 4.2 Database engine: SQLite only, but kept portable

**There is no added value in offering a SQLite/Postgres choice here, and a real
cost in offering it** — a second backend doubles the migration and test surface,
and asks every third-party builder to make a decision they have no basis for.

The scale argues plainly: a handful of devices and accounts, a few messages a
day. SQLite's only structural limit is its single writer, and with WAL mode
readers never block it. Our writes are message inserts, delivery transitions and
heartbeats — telegraphy isn't even persisted (Q32). Postgres wins on multiple
app instances, heavy concurrent writes and a network-separated database; none of
those are in this project's future. Meanwhile SQLite is one file, which makes
backups *easier* (`sqlite3 .backup`, or Litestream for continuous copies).

So: **ship SQLite, and spend the small effort that keeps Postgres a
`DATABASE_URL` change rather than a rewrite.** Three concrete rules:

1. **Alembic for migrations**, not telex's idempotent
   `ALTER TABLE … ADD COLUMN` wrapped in `try/except` (`database.py:_migrate`).
   That pattern cannot rename a column, change a type or migrate data, and it
   silently swallows genuine errors. Alembic is portable and reversible, and
   this project will have real schema evolution.
2. **No backend-specific SQL.** Everything through SQLModel/SQLAlchemy
   constructs; no SQLite-only functions or pragmas in query code.
3. **Async driver: `aiosqlite` with async SQLAlchemy sessions.** This one is not
   about portability, it is correctness: the server holds long-lived WebSockets,
   and a synchronous DB call on the event loop stalls *every* connected box, not
   just one request. (Telex got away with sync sessions because it was pure
   request/response.) The same choice maps to `asyncpg` if Postgres ever
   happens.

SQLite is opened with WAL, `foreign_keys=ON` and a `busy_timeout`.

---

## 5. Control-plane protocol

### 5.1 Device ↔ server WebSocket

One connection, `wss://<server>/api/device/ws`. Credentials on the HTTP upgrade
(`X-Device-ID`, `X-Device-Secret`). JSON frames, `{"t": "<type>", ...}`.
Heartbeat: server `ping` every 20 s, device must answer within 10 s; the device
reconnects with exponential backoff capped at 30 s.

**Server → device**

| `t` | Payload | Effect |
|---|---|---|
| `hello` | `server_time`, `channel`, `alias`, `paper_cols`, `settings` | Clock source (F3) and config refresh |
| `telex` | `delivery_id`, `sent_at`, `sender`, `text`, `image_url`, `image_mode` | Spool and print |
| `telegraphy` | `active: bool`, `from: alias` | Orange LED on/off + local bip |
| `voip` | `action: join|leave`, `room`, `rtp: {...}` | Join/leave the AudioBridge room |
| `command` | `reprint_ticket | test_print | check_update | reboot` | From the admin console |
| `ping` | — | |

**Device → server**

| `t` | Payload |
|---|---|
| `hello` | `fw_version`, `printer_info`, `paper_cols`, `ip`, `mac`, `uptime` |
| `ack` | `delivery_id`, `status: printed|failed`, `error` |
| `telegraphy` | `pressed: bool` |
| `ptt` | `active: bool` (presence/UI only — media goes to Janus, not here) |
| `status` | `printer_ok`, `queue_depth`, `wifi_rssi`, `cpu_temp` |
| `pong` | — |

**Catch-up on connect** (Q3, Q19): after `hello` the server pushes every
`pending` delivery for that device, which is what makes "print what arrived
while we were off" work without a polling loop.

### 5.2 Telegraphy fan-out

A press is broadcast to every *other* member of the channel (devices and web
clients) and is **not persisted** (Q32). The server tracks a per-channel set of
currently-pressing participants and sends `telegraphy{active}` on set
empty↔non-empty transitions only, so a device holding the button doesn't
generate traffic and a second presser doesn't retrigger the first's LED.

### 5.3 HTTP API

| Auth | Prefix | Used by |
|---|---|---|
| session cookie + TOTP | `/api/admin/*` | admin console |
| session cookie | `/api/client/*` | web clients |
| `X-Device-ID` + `X-Device-Secret` | `/api/device/*` | boxes (provisioning, image fetch, ACKs) |

Routes are explicit — telex's catch-all `@app.get("/{filename}")` (F4.2) is
dropped. Page routes: `/admin`, `/{channel}`, `/{channel}/{device}`, with a
reserved-slug list so no channel can be called `admin`, `api` or `static`.

---

## 6. Media plane

### 6.1 Janus AudioBridge

One **AudioBridge room per channel** (Q5c), created on demand from the channel
definition so adding a channel needs no Janus configuration by hand. Room
config: `sampling_rate = 8000`, `allow_rtp_participants = true`, recording
disabled.

- **Devices** join as **plain RTP participants** with `codec: pcmu` — the `rtp`
  object in the join request carries the device's tunnel IP and port. No
  WebRTC, DTLS, ICE or SRTP on the Pi; µ-law is a lookup table, so codec cost is
  effectively zero.
- **Browsers** join the same room as ordinary WebRTC participants (Opus), and
  Janus transcodes into the mix.
- The server drives Janus over its HTTP API on localhost. Janus's own admin
  surface is never exposed.
- **Two independent RTP port ranges**, verified against Janus 1.4.2 during the
  phase 0b spike and worth stating because it is a silent failure mode:
  plain-RTP participants are bound from `rtp_port_range` in
  **`janus.plugin.audiobridge.jcfg`** (default 10000+), while
  `media.rtp_port_range` in `janus.jcfg` governs only ICE/WebRTC media. Both
  must be set, and both opened — on the tunnel for the devices, and on the
  public interface for browsers in `plain_rtp` mode. Getting this wrong
  produces audio that is silent with no error in any log.
- The `ip` Janus reports in a join response is **its own view of itself** (a
  container or tunnel address), not necessarily one the device can reach. The
  device always sends to the address it was given in its configuration.
- Mixing is server-side, so each box receives exactly **one** stream regardless
  of how many participants exist, and **nobody is ever sent their own audio** —
  "except loopback" is free.
- Two simultaneous PTTs are simply mixed (Q7).

### 6.2 Device audio pipeline (GStreamer)

```
TX (while PTT held):
  alsasrc ! audioconvert ! audioresample ! audio/x-raw,rate=8000,channels=1
          ! mulawenc ! rtppcmupay ! udpsink host=<janus-wg-ip> port=<p>

RX (always):
  udpsrc port=<q> ! application/x-rtp,encoding-name=PCMU
          ! rtpjitterbuffer latency=60 ! rtppcmudepay ! mulawdec
          ! audioconvert ! audioresample ! alsasink
```

All elements come from Debian armhf packages. **We write no jitter buffer** —
`rtpjitterbuffer` is mature and tunable, and it was the main thing I had
wrongly credited Mumble with providing.

Latency budget against the < 150 ms target (< 500 ms worst case, Q10): 20 ms
frame + 60 ms jitter buffer + network RTT + ~20 ms ALSA. The jitter buffer is
the tuning knob the spike exists to set.

### 6.3 Half-duplex and the bip

- PTT held → TX starts, **local speaker muted** (Q6). Released → TX stops,
  speaker unmuted.
- The **bip** is generated locally on the Pi (Q9) — telegraphy sends only
  press/release events, never audio. Default 880 Hz with a short
  attack/release to avoid clicks; frequency, volume and on/off live in the local
  console.
- Bip and VoIP playback are mixed through ALSA **`dmix`**, since the UGREEN card
  won't mix in hardware.

### 6.3.1 Output level and speaker protection

The speakermic holds a small, cheap driver behind a 22 Ω series resistor, and
what kills a driver like that is **sustained** level, not peaks. So the design
puts a hard ceiling below every user-facing control:

| Setting | Where it acts | Range |
|---|---|---|
| `volume_max_pct` | **ALSA hardware mixer** | 0–100%, default from the §3.5 bring-up sweep |
| `voip_volume_pct` | software gain, VoIP branch | 0–100% **of the ceiling** |
| `bip_volume_pct` | software gain, bip generator | 0–100% **of the ceiling** |

Why the ceiling lives in the hardware mixer rather than in software gain: the
mixer is the last stage before the amplifier, so it bounds the analogue output
**regardless of what any software does** — a bug in the bip generator or a
malformed RTP payload cannot exceed it. Software gain alone would leave the amp
running at full scale and merely hope the samples stay small.

Conversely the two user controls are software gains, because the bip and VoIP
need *independent* levels and there is only one mixer.

Rules:

- `bipboxd` owns the mixer: it asserts `volume_max_pct` at startup and after any
  change, so nothing else drifts it. ALSA state is also saved across reboots.
- The mapped (perceptual) ALSA scale is used — `amixer -M` — so a percentage
  behaves like a percentage on a slider rather than like a register value.
- Raising `volume_max_pct` shows a **warning** in the local console naming the
  risk to the speaker, and it is deliberately not exposed in the ordinary volume
  UI. Above 70% the warning is stronger.
- Boxes ship at `volume_max_pct = 40` until the sweep says otherwise, so a
  freshly flashed card can never arrive loud.
- If the sweep concludes that even ~20% is too loud, that is a **hardware**
  answer, not a software one: solder the 47 Ω in parallel with the 22 Ω and
  attenuate there, rather than running the amplifier at the very bottom of its
  range where SNR is worst.

The same ceiling applies to the test tone and the printer-less startup beep —
there is no code path to the speaker that bypasses it.

### 6.4 Browser media — selectable transport mode

Browser WebRTC media is UDP and cannot traverse the Cloudflare proxy, so the
browser must reach Janus directly. There are two ways to let it, and since
different deployments of a published project will want different answers, the
mode is an **admin setting** rather than an architectural decision:

| `browser_voip_mode` | Media path | Public IP disclosed? |
|---|---|---|
| `off` | — | no |
| `wireguard` *(recommended default)* | Browser's OS tunnel → `wg0` → Janus | **no** |
| `plain_rtp` | Browser → public IP:UDP range → Janus | yes, to authenticated users |

The modes are **mutually exclusive, not a fallback chain.** It is tempting to
let Janus gather on both interfaces and let ICE pick the winner — but then the
public IP is in every SDP, which destroys the entire point of tunnel mode. So
the mode drives Janus's ICE configuration:

- `wireguard` → `ice_enforce_list = "wg0"`. Janus gathers candidates on that
  interface **only**, so the public IP *cannot* appear in the SDP. This is a
  hard guarantee from Janus, not a convention we have to maintain.
- `plain_rtp` → enforce the public interface, with `nat_1_1_mapping` set to the
  static IP.

Changing the mode rewrites `janus.jcfg` and restarts Janus, which drops any call
in progress. It is a deployment setting, not a hot toggle, and the admin console
says so.

#### 6.4.1 Browser VoIP over WireGuard

The browser has no WireGuard of its own, but the user's **operating system**
does, and the routing table is all that's needed: with `10.8.0.0/24` routed over
`wg0`, the browser's WebRTC media to Janus at `10.8.0.1` goes through the tunnel
with no browser involvement at all. Signalling stays on Cloudflare WSS.

Design details that matter:

- **Split tunnel, always.** `AllowedIPs = 10.8.0.0/24` only. The user's ordinary
  browsing is untouched, which is the difference between "a toggle I'll accept"
  and "a VPN I refuse to install".
- **One peer per *device*, not per account.** WireGuard tracks a single endpoint
  per public key, so the same config active on a phone and a laptop makes the
  two fight over the peer. The client console therefore lets an account add,
  name and revoke several devices ("Pixel", "MacBook"), each with its own
  keypair and tunnel IP.
- **QR-code onboarding.** The WireGuard mobile apps import a config by scanning
  a QR, so mobile setup is: install app → scan → toggle. That is genuinely
  within reach for a grandparent; desktop needs an installer and admin rights,
  which is the harder case. Both the `.conf` download and the QR are served from
  the client console, alongside a short illustrated tutorial per platform.
- **ICE works even with Chrome's mDNS obfuscation.** Chrome replaces local host
  candidates with `.local` mDNS names unless the page holds media permission.
  It does not matter here: Janus learns the browser's real tunnel address from
  the source of the inbound STUN connectivity check (a peer-reflexive
  candidate), and a voice page has microphone permission anyway.
- **Key generation is server-side**, and that is a real if modest trade: the
  server mints the keypair, hands over the config once, stores only the public
  key and does not retain the private key. Generating it in-browser via WebCrypto
  X25519 is possible but the user would have to paste it into a config by hand,
  which trades a small trust assumption for a large usability cost. (This is how
  VPN portals generally work.)
- **Dynamic peer management is required**, since accounts and their devices come
  and go: the server runs `wg set wg0 peer <pubkey> allowed-ips <ip>` and
  allocates from an IP pool, with no restart and no dropped calls.

  **This couples OPEN-1 to OPEN-2**: live peer management is simple when
  WireGuard sits beside the server (shared netns or a tiny privileged sidecar)
  and awkward when it terminates on the OpenWRT VM, which would then need its
  own authenticated API for the server to call. It is an argument for the
  co-located default.

Honest caveats: the user must remember to switch the tunnel on before talking
(the client console detects reachability and says so plainly), and this is the
one feature that asks a non-technical relative to install software.

#### 6.4.2 Plain-RTP mode and the firewall webhooks

When `plain_rtp` is selected, the admin console reveals a panel explaining that
the RTP port range must be reachable, and offers **two generic webhooks** — one
on session start, one on session end — so the firewall can be opened per client
without bipbox knowing anything about OpenWRT:

| Field | Notes |
|---|---|
| URL | **Jinja template too** — many router APIs are `GET /open?ip={{ CLIENT_IP }}` |
| Method | GET / POST / PUT / DELETE |
| Headers | key/value map, for tokens |
| Auth | none / basic / bearer |
| Allow invalid TLS | explicit toggle, for routers with self-signed certs |
| Body | Jinja template |
| Timeout, retries | defaults 5 s, 2 |
| **Test** | renders the template with sample values and shows the real request and response |

That test button is what makes this feature usable instead of maddening, so it
is part of the first implementation, not a nicety.

Template variables: `EVENT` (`session_start`/`session_end`), `CLIENT_IP`,
`CLIENT_ALIAS`, `CLIENT_LOGIN`, `ACCOUNT_ID`, `CHANNEL_SLUG`, `SESSION_ID`,
`TIMESTAMP` (ISO-8601), `TIMESTAMP_EPOCH`, `EXPIRES_AT`, `RTP_PORT_START`,
`RTP_PORT_END`, `USER_AGENT`.

Three hardening requirements, because this feature turns an HTTP request into a
firewall change:

1. **`CLIENT_IP` must come from a trusted proxy only.** Behind Cloudflare it is
   `CF-Connecting-IP`, and it is honoured *only* when the peer address is in
   Cloudflare's published ranges. Telex's `_real_ip()` takes the first
   `X-Forwarded-For` entry unconditionally — ported as-is, a user could spoof
   the header and have your firewall opened for an address of their choosing.
   This is the most security-sensitive line of code in the project.
2. **Templates render in a Jinja `SandboxedEnvironment`**, with variables
   JSON-escaped. The templates are admin-authored, but the admin console is
   precisely what we protected with TOTP, so it should not also be a code
   execution primitive.
3. **Expiry is authoritative; `session_end` is best-effort.** Browsers close
   without logging out, so a rule that only closes on a session-end webhook
   leaks open rules forever. `EXPIRES_AT` is passed so the firewall sets its own
   timeout, the session renews it while the page is alive, and the session-end
   hook is an early close rather than the mechanism.

Final caveat worth stating in the UI: on mobile carriers (CGNAT) and in offices,
`CLIENT_IP` is shared by many subscribers, so "open for the client's IP" is a
much weaker control than it sounds.

A fourth mode — **coturn relaying on 443/TLS** — is documented as a future
option for people who can do neither, and is not in v1.

### 6.5 WireGuard: embedded, with a tunable subnet

**Decided: WireGuard runs inside the server deployment** (a container sharing
the server's network namespace, so `wg set` works without a privileged sidecar).
The whole stack is one `docker-compose.yml`, which is what makes it publishable,
and live peer management for browser clients (§6.4.1) stays trivial.

One non-standard high UDP port, no DNS record, and **no port knocking** —
WireGuard is already silent to unauthenticated packets, which is what knocking
only pretends to achieve.

#### Subnet choice

You're right that this must be tunable, and the default should not be in
`10.0.0.0/8` at all. Three ranges are poor defaults for distinct reasons:

- **`10.x`** — your own network uses it extensively, and so do many others.
- **`172.16/12`** — Docker allocates its bridge networks from `172.17`–`172.31`,
  and we are *running in Docker*.
- **`192.168.x`** — almost every client's home LAN. A split-tunnel route that
  collides with the user's own network breaks their internet, not just bipbox.

→ **Default `100.64.42.0/24`**, inside RFC 6598 CGNAT shared space — the same
reasoning (and the same range) Tailscale uses, precisely because it collides
with neither corporate `10/8` nor home `192.168/16`. Set by `WG_SUBNET`.

A `/24` rather than something larger keeps the client-side route footprint as
small as possible. Note that the subnet is **deploy-time**: changing it reissues
every peer config, device and browser alike, so it is documented as a decision
to make once at install.

A monitoring note that follows from WireGuard's silence: **do not point an
Uptime Kuma TCP or ping monitor at the WireGuard port.** It will always report
down, correctly — the port does not answer unauthenticated packets by design.
Monitor peer handshake ages instead (§13).

---

## 7. Telex (printing)

### 7.1 Rendering and dithering

Per-device `paper_cols` / `paper_dots` (Q17), defaulting to 42 / 576 for your
two 80 mm printers. The compose UI renders at the **recipient's** width, and
warns when a multi-recipient send mixes widths.

On your point about the 1-bit threshold: you were right for the content you were
testing. Threshold and dithering are better at *different* things, so we pick
per source rather than globally —

| Source | Default | Why |
|---|---|---|
| Canvas drawing | **threshold** | Flat fills and clean lines; dithering would add noise to art that is already 1-bit in spirit. |
| Uploaded photo | **Floyd–Steinberg** | Continuous tone; threshold crushes it to blobs. |

…with a manual toggle, and `Message.image_mode` persisted so a reprint is
identical to the original.

### 7.2 Upload limits (Q18, Q21)

You asked for the distinction, and it's two separate limits:

| Stage | Limit |
|---|---|
| **Accepted from the browser** | generous — 25 MB / 50 MP. Nobody should pre-resize a phone photo. |
| **Resized server-side** | to `paper_dots` wide, then dithered |
| **Delivered to the device** | ~600 kB cap, as today — but it is now the *output* of resizing, so it can't be hit by accident |

Text stays at 500 characters (≈ 12 lines at 42 columns — a sensible ticket).
Preview is **generated server-side and returned as the dithered bitmap**, so the
on-screen preview is pixel-identical to the print rather than an approximation.

### 7.3 Queue

The server is the queue (Q19); `Delivery.status` follows
`pending → delivered → printed | failed`. The device keeps a small local spool
for received-but-not-yet-printed messages, retries every 30 s, and replays after
a reboot or a paper jam. Printer detection, the `usblp` blacklist, the udev
rules and the ESC/POS layout port directly from telex — that code was hard-won
and works.

---

## 8. Security

| Surface | Protection |
|---|---|
| `/admin` | Argon2id + **TOTP**, single admin account (Q15), server-side session, escalating fail2ban, optionally Cloudflare Access |
| `/{channel}` client login | Argon2id, hard fail2ban (telex's ladder: 3/15 min → 1 h, 5/1 h → 72 h, 7/48 h → permanent) |
| Device WebSocket | UUID + 190-bit secret, HMAC-SHA256 verification, **soft** rate limiting — log and alert, never a permanent ban (F4.3) |
| Media plane | WireGuard for devices; see **[OPEN-1]** for browsers |
| Janus admin API | localhost only, never exposed |
| Alerts | SMTP **and** ntfy (Q44): permanent bans, account locks, device offline > N minutes |

Anonymous sending is **gone** (Q13) — every human has an account. That removes
telex's "send password" concept entirely.

---

## 9. Device provisioning and image

### 9.1 `bipbox.conf` on the boot partition (Q25)

Plain `key=value` on the FAT32 partition, editable from any OS without mounting
ext4:

```ini
wifi_ssid=…
wifi_psk=…
wifi_country=FR
hostname=bipbox-arthur
server_url=https://…
device_uuid=…
device_secret=…
```

Read at first boot, applied, then the secrets are blanked in place (the file
stays, so it's obvious it was consumed). A box can thus be fully provisioned by
editing one file on the card — no keyboard, no screen, no SSH.

### 9.2 First boot (Q35)

`bipbox-firstboot.service`, oneshot: generate the UUID if absent → set hostname
(default `bipbox-XXXX` from the UUID, Q24) → regenerate SSH host keys → expand
the filesystem → apply `bipbox.conf` → print the setup ticket (IP, hostname, AP
SSID `bipbox-XXXX`, local-console password, QR code) → enable the services.

### 9.3 Network states

NetworkManager/nmcli, DHCP only (Q26). On boot: try known networks for 120 s →
on success, `wlan0` station mode; on failure, AP `bipbox-XXXX` on 192.168.4.1
with the console open for setup. mDNS via avahi as `<hostname>.local`.

Telex's hotspot path is rewritten, not ported — it was dead code (F4.1).

### 9.4 Image build (Q34)

**pi-gen** in Docker, producing a versioned `.img.xz`: Raspberry Pi OS **Lite
Bookworm armhf** (Q33 — ARMv6 means 32-bit only). Golden-image `dd` is dropped.

Updates: **no auto-update** (Q36). A "check for update" button in the local
console queries the public GitHub releases API and applies on explicit
confirmation. The repo is public, so no token ever sits on an SD card.

### 9.5 Local console (Q22)

Flask on port 80. Read-only status page open; **every write needs the local
admin password**, generated at first boot and printed on the setup ticket. In
AP-mode first-run it is necessarily open, which is the one unavoidable window.

Exposes: WiFi (scan/add/remove), hostname, server URL + device credentials,
printer settings + test print, VoIP volume, bip on/off + frequency + volume,
reprint ticket, check for update, logs.

---

## 10. Internationalisation (Q41)

FR **and** EN, which rules out my earlier "no i18n framework" line. But Babel
or gettext is heavier than this needs.

→ **Decided: one flat JSON catalogue per locale** (`locales/fr.json`, `locales/en.json`),
loaded server-side for template rendering and served to the browser for the JS
strings — a single source of truth, no extraction toolchain, no build step.
Locale from `Accept-Language`, overridable by a switcher persisted in a cookie.

Code and comments in English throughout. Start on telex's amber/green phosphor
theme; you'll likely replace it with something matched to the bipbox logo, so
colours and fonts live in CSS custom properties from day one to make that a
restyle rather than a rewrite.

---

## 11. Repo

```
bipbox/
├── server/          # FastAPI app, Dockerfile, docker-compose.yml
├── device/          # bipboxd daemon + Flask local console
├── image/           # pi-gen config, first-boot, systemd units
├── docs/            # context, architecture, specs, hardware notes
└── CAD/             # KiCad + FreeCAD (you commit these yourself)
```

`git init` as a fresh public repo (Q37). `.gitignore`: `telex/` (reference only,
deleted once the port is done), `.CAD_orig/` (dead, kept locally for
reference), `*.FCBak`, `__pycache__/`, `.env`, `server/data/`. KiCad sources and
exported PDF/STEP are committed; `.FCStd` via git-lfs (Q38).

Ported telex code carries attribution in its commit message.

---

## 12. Phasing (Q45, with Q43 folded in)

| Phase | Contents |
|---|---|
| **0a** | **`wiring_test.py`** (§3.5) — standalone HAT bring-up tool. No dependency on anything else, so it ships first and unblocks hardware iteration immediately. |
| **0b** | **VoIP spike** — Janus + AudioBridge in Docker, 8 kHz PCMU room, Pi joins via GStreamer over WireGuard. Measure CPU and mouth-to-ear latency with 2–3 participants, then compare 16 kHz Opus. Gates the whole media design. |
| **1** | Foundations — repo, server skeleton (channels/devices/accounts, admin + TOTP), `bipboxd` skeleton with LEDs/buttons/SPI and `--fake-hardware` so it runs on your Mac |
| **2** | Telex — printer detection, ESC/POS layout, drawing/photo + dithering, queue + receipts, client send page |
| **3** | Telegraphy — WS events, orange LED, local bip, web button |
| **4** | Local console + WiFi/AP + `bipbox.conf` |
| **5** | VoIP — device audio, Janus orchestration, half-duplex PTT, **and browser VoIP** (no longer v2, since AudioBridge makes browsers native participants) |
| **6** | Image — pi-gen, first boot, flashing docs |

Phase 0 is new and goes first: it is the only part that can invalidate an
architectural choice, it needs no bipbox code at all, and you have the hardware
on hand.

---

## 13. Observability — Uptime Kuma

You run Uptime Kuma on a VPS, so health is exposed in the shape Kuma consumes
best: **HTTP status codes doing the work**, so each monitor needs no JSON query.

| Endpoint | Auth | Returns |
|---|---|---|
| `GET /health` | none | `200` + `{"status":"ok"}`. Liveness only, deliberately cheap — no DB hit. |
| `GET /health/ready` | none | `200` when DB and Janus both answer, `503` otherwise |
| `GET /health/device/{alias}` | token | `200` when that box is online, `503` when not — **one Kuma monitor per bipbox**, giving a per-box uptime graph for free |
| `GET /health/detail` | token | full JSON tree, for a Kuma "Json Query" monitor or for eyeballing |

`/health/detail` reports, each with its own `ok` flag: database, Janus (via its
API), WireGuard peers with **handshake age** per peer, every device
(`online`, `last_seen`, `queue_depth`, `printer_ok`, `fw_version`), pending and
failed deliveries in the last 24 h, image-store free space, and active bans.

Three rules for these endpoints:

- The token is a dedicated `MONITOR_TOKEN`, **not** the admin session and
  **not** behind TOTP — Kuma cannot do TOTP. It is read-only and grants nothing
  else.
- They are exempt from the fail2ban ladder, or a monitoring blip would ban your
  VPS.
- `/health` must not touch the database, so that "the app is up" and "the
  database is healthy" stay separately diagnosable — which is the entire point
  of having both it and `/health/ready`.

Per-device alerts (Q44) stay in the server via SMTP and ntfy; Kuma is the
outside-in view, deliberately duplicating it, since a server that is wedged
cannot alert on itself.

Prometheus `/metrics` is not included — Kuma does not scrape it. Easy to add if
Grafana ever appears.

---

## 14. Open decisions

**None.** All resolved as of 2026-10-03:

1. ~~Browser media vs. public-IP disclosure~~ → `browser_voip_mode` setting with
   `off` / `wireguard` / `plain_rtp`, the last carrying generic firewall
   webhooks (§6.4). Default `wireguard`, which discloses nothing.
2. ~~Where WireGuard terminates~~ → embedded in the server deployment, subnet
   tunable via `WG_SUBNET`, default `100.64.42.0/24` (§6.5).
3. ~~I18n approach~~ → flat JSON catalogue per locale (§10).
4. ~~SQLite vs Postgres~~ → SQLite only, kept portable via Alembic, no
   backend-specific SQL, and an async driver (§4.2).
