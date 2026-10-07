#!/usr/bin/env python3
"""Join a Janus AudioBridge room as a plain RTP participant.

Standard library only, so it runs on a bare Raspberry Pi OS Lite with no pip
installs. Keeps the Janus session alive and prints every event verbatim, which
is what you want from a spike tool.

Verified against Janus 1.4.2 / AudioBridge 0.0.13: the "joined" event carries
`rtp: {ip, port, payload_type}`, where the port is allocated from the
*plugin's* own rtp_port_range and the ip is Janus's view of itself — see
RESULTS.md.

    python3 audiobridge_join.py --janus http://192.168.1.10:8088 \
        --local-ip 192.168.1.50 --local-port 5004 --display pi-a

It then prints the GStreamer commands to run, with the discovered ports filled
in, and holds the session open until Ctrl-C.
"""

import argparse
import json
import random
import string
import sys
import urllib.error
import urllib.request

PLUGIN = "janus.plugin.audiobridge"
OPUS_PAYLOAD_TYPE = 111


def transaction():
    return "".join(random.choices(string.ascii_letters + string.digits, k=12))


def post(url, payload, timeout=40):
    body = json.dumps(payload).encode()
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code} from {url}: {e.read().decode(errors='replace')[:400]}")
    except urllib.error.URLError as e:
        sys.exit(f"Cannot reach {url}: {e.reason}")


def get(url, timeout=40):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code} from {url}: {e.read().decode(errors='replace')[:400]}")
    except urllib.error.URLError as e:
        sys.exit(f"Cannot reach {url}: {e.reason}")


def find_key(obj, wanted):
    """Depth-first search for a key, so a shape change upstream degrades gracefully."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key == wanted:
                return value
            found = find_key(value, wanted)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = find_key(item, wanted)
            if found is not None:
                return found
    return None


def plugin_error(event):
    data = event.get("plugindata", {}).get("data", {})
    if isinstance(data, dict) and ("error" in data or "error_code" in data):
        return data.get("error_code"), data.get("error")
    return None


def print_pipelines(args, janus_host, janus_port):
    """Print the two pipelines, which is the whole point of having joined."""
    if args.codec == "pcmu":
        encode = "audio/x-raw,rate=8000,channels=1 ! mulawenc ! rtppcmupay pt=0"
        decode = (
            f"application/x-rtp,media=audio,clock-rate=8000,encoding-name=PCMU,payload=0 "
            f"! rtpjitterbuffer latency={args.jitter} ! rtppcmudepay ! mulawdec"
        )
    else:
        encode = (
            f"audio/x-raw,rate=16000,channels=1 ! opusenc bitrate=24000 "
            f"frame-size={args.frame} ! rtpopuspay pt={OPUS_PAYLOAD_TYPE}"
        )
        decode = (
            f"application/x-rtp,media=audio,clock-rate=48000,encoding-name=OPUS,"
            f"payload={OPUS_PAYLOAD_TYPE} "
            f"! rtpjitterbuffer latency={args.jitter} ! rtpopusdepay ! opusdec"
        )

    print("\n" + "=" * 72)
    print("TX — run while PTT is held (Ctrl-C to stop talking):")
    print("=" * 72)
    print(
        f"gst-launch-1.0 -q alsasrc device={args.alsa} ! audioconvert ! audioresample"
        f" ! {encode} ! udpsink host={janus_host} port={janus_port}"
    )
    print("\n" + "=" * 72)
    print("RX — run continuously:")
    print("=" * 72)
    print(
        f"gst-launch-1.0 -q udpsrc port={args.local_port} caps='{decode}'"
        f" ! audioconvert ! audioresample ! alsasink device={args.alsa}"
    )
    print("=" * 72 + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--janus", required=True, help="e.g. http://192.168.1.10:8088")
    parser.add_argument("--room", type=int, default=1000)
    parser.add_argument("--local-ip", required=True, help="where Janus should send RTP")
    parser.add_argument("--local-port", type=int, default=5004)
    parser.add_argument("--display", default="spike")
    parser.add_argument("--codec", choices=("pcmu", "opus"), default="pcmu")
    parser.add_argument("--alsa", default="plughw:1,0", help="ALSA device for the pipelines")
    parser.add_argument("--jitter", type=int, default=60, help="rtpjitterbuffer latency, ms")
    parser.add_argument("--frame", type=int, default=20, help="Opus frame size, ms")
    args = parser.parse_args()

    base = args.janus.rstrip("/") + "/janus"
    janus_host = args.janus.split("//", 1)[-1].split(":")[0].split("/")[0]

    session = post(base, {"janus": "create", "transaction": transaction()})
    session_id = session["data"]["id"]
    print(f"session   {session_id}")

    attached = post(
        f"{base}/{session_id}",
        {"janus": "attach", "plugin": PLUGIN, "transaction": transaction()},
    )
    handle_id = attached["data"]["id"]
    print(f"handle    {handle_id}")

    rtp = {"ip": args.local_ip, "port": args.local_port}
    if args.codec == "opus":
        rtp["payload_type"] = OPUS_PAYLOAD_TYPE

    join = {
        "janus": "message",
        "transaction": transaction(),
        "body": {
            "request": "join",
            "room": args.room,
            "display": args.display,
            "codec": args.codec,
            "rtp": rtp,
        },
    }
    print(
        f"joining   room {args.room} as {args.display} ({args.codec}) → "
        f"{args.local_ip}:{args.local_port}"
    )
    ack = post(f"{base}/{session_id}/{handle_id}", join)
    if ack.get("janus") == "error":
        sys.exit(f"join rejected: {json.dumps(ack, indent=2)}")

    janus_port = None
    print("\nwaiting for events (Ctrl-C to leave)…\n")
    try:
        while True:
            event = get(f"{base}/{session_id}?maxev=1")
            kind = event.get("janus")
            if kind == "keepalive":
                continue

            print(json.dumps(event, indent=2))

            failure = plugin_error(event)
            if failure:
                code, message = failure
                print(f"\n!! AudioBridge refused the join: [{code}] {message}")
                if code == 489 or (message and "rtp" in str(message).lower()):
                    print(
                        "   Check allow_rtp_participants=true on the room "
                        "(janus.plugin.audiobridge.jcfg)."
                    )
                sys.exit(1)

            if janus_port is None:
                rtp_info = find_key(event.get("plugindata", {}), "rtp") or {}
                port = rtp_info.get("port") or find_key(event.get("plugindata", {}), "port")
                if port:
                    janus_port = port
                    advertised = rtp_info.get("ip")
                    if advertised and advertised != janus_host:
                        # Janus reports the address it sees on itself, which
                        # behind Docker (or NAT, or WireGuard) is not what the
                        # Pi can reach. The host from --janus is authoritative.
                        print(
                            f"\n   note: Janus advertises {advertised}, which is its own "
                            f"view;\n         sending to {janus_host} instead."
                        )
                    print(f"\n>> Janus expects our RTP on {janus_host}:{janus_port}")
                    print_pipelines(args, janus_host, janus_port)
    except KeyboardInterrupt:
        print("\nleaving room…")
        post(
            f"{base}/{session_id}/{handle_id}",
            {"janus": "message", "transaction": transaction(), "body": {"request": "leave"}},
            timeout=5,
        )
        post(f"{base}/{session_id}", {"janus": "destroy", "transaction": transaction()}, timeout=5)
        return 0


if __name__ == "__main__":
    sys.exit(main())
