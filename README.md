# Bipbox

A self-hosted communication device for children — a wooden box with a thermal
printer, a telegraph key and a walkie-talkie, so that two kids in two different
homes can write, beep and talk to each other.

> **Status: in development.** The hardware exists (two boxes built); the
> software is being written. See [`docs/architecture.md`](docs/architecture.md).

## What it does

| Feature | |
|---|---|
| **Telex** | Family members log into a web page and send a text, a drawing or a photo. It prints on the box's thermal printer within seconds. |
| **Telegraphy** | Press the button on your box and the orange lamp lights up on the other one, with an optional beep. Nothing more — that is the charm. |
| **VoIP** | Squeeze the PTT on the shoulder speakermic and talk; your voice comes out of the other box's speaker. |

Boxes are grouped into **channels**. Everyone in a channel can beep and talk to
each other. People without a box can join from a browser.

## Hardware

Each box contains a Raspberry Pi Zero W, a MeanWell RS15-5 supply, a 4-port USB
hub, a USB sound card, an ESC/POS thermal printer and a homemade HAT that drives
four status lamps through a 74HCT595 and reads the buttons.

KiCad and FreeCAD sources are in [`CAD/`](CAD/) so you can build your own.

## Repository layout

```
server/   FastAPI server + Janus, deployed with docker-compose
device/   bipboxd daemon and the Pi's local management console
image/    pi-gen configuration producing a ready-to-flash SD image
docs/     architecture, specifications, hardware notes
CAD/      KiCad (HAT) and FreeCAD (enclosure)
```

## Documentation

| | |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | the design: topology, pin map, data model, protocol, security |
| [`docs/voip-options.md`](docs/voip-options.md) | why the voice path is Janus AudioBridge and not Mumble or WebRTC |
| [`docs/open-questions.md`](docs/open-questions.md) | the decisions behind the design, and why |
| [`docs/workflow.md`](docs/workflow.md) | branches, commits, releases |
| [`context.md`](context.md) | the original description of the project |

## Hardware bring-up

Before any of the software exists, the HAT can be tested on its own:

```bash
sudo apt install python3-spidev python3-gpiozero
sudo python3 device/tools/wiring_test.py
```

It discovers the shift-register bit order, measures button bounce, finds a
speaker-safe volume ceiling and prints a test ticket. See
[architecture.md §3.5](docs/architecture.md).

## Licensing

Two licences, scoped by what they cover:

| | |
|---|---|
| **Software** — everything outside `CAD/` | [MIT](LICENSE) |
| **Hardware** — the KiCad and FreeCAD sources in `CAD/` | [CERN-OHL-S v2](CAD/LICENSE) (strongly reciprocal) |

So you may do as you like with the code, and if you build on the board or the
enclosure and distribute the result, those improvements stay open.

## Acknowledgements

Bipbox grows out of [telex](https://github.com/gbwebdev/telex), an earlier
message-to-thermal-printer project; its printer handling, ESC/POS layout and
drawing tool are carried over here.
