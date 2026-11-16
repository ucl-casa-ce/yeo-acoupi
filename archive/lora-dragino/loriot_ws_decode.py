#!/usr/bin/env python3
"""Live-decode acoupi BirdNET LoRa uplinks from a LORIOT WebSocket output.

Connects to a LORIOT application WebSocket, decodes our compact payload
(see lora-dragino/loriot-payload-decoder.md), and prints readable detections.

Usage:
    pip install websocket-client
    python loriot_ws_decode.py "wss://eu1.loriot.io/app?token=<YOUR_APP_TOKEN>"

The URL/token comes from LORIOT: Output -> enable the WebSocket output (or
Access Tokens -> create an application token). Passing it as an argument keeps
the token out of this file (don't hard-code/commit it).
"""
import json
import os
import struct
import sys
import time

import websocket  # pip install websocket-client

FPORT = 2

# id -> name. KEEP IN SYNC with SPECIES_LUT in acoupi_lora_bridge.py and the
# SPECIES map in loriot-payload-decoder.md.
SPECIES = {
    1: "Common Wood-Pigeon (Columba palumbus)",
    2: "Carrion Crow (Corvus corone)",
    3: "American Crow (Corvus brachyrhynchos)",
    4: "Eurasian Collared-Dove (Streptopelia decaocto)",
    5: "Eurasian Blackbird (Turdus merula)",
    6: "European Robin (Erithacus rubecula)",
    7: "Eurasian Blue Tit (Cyanistes caeruleus)",
    8: "Great Tit (Parus major)",
    9: "House Sparrow (Passer domesticus)",
    10: "Common Chaffinch (Fringilla coelebs)",
    11: "European Starling (Sturnus vulgaris)",
    12: "Eurasian Magpie (Pica pica)",
    13: "Eurasian Wren (Troglodytes troglodytes)",
    14: "Dunnock (Prunella modularis)",
    15: "European Goldfinch (Carduelis carduelis)",
    16: "European Greenfinch (Chloris chloris)",
    17: "Common Chiffchaff (Phylloscopus collybita)",
    18: "Eurasian Blackcap (Sylvia atricapilla)",
    19: "Song Thrush (Turdus philomelos)",
    20: "Long-tailed Tit (Aegithalos caudatus)",
    21: "Eurasian Jackdaw (Corvus monedula)",
    22: "Rook (Corvus frugilegus)",
    23: "Eurasian Jay (Garrulus glandarius)",
    24: "Common Swift (Apus apus)",
    25: "Barn Swallow (Hirundo rustica)",
    26: "European Herring Gull (Larus argentatus)",
    27: "Mallard (Anas platyrhynchos)",
    28: "Common Buzzard (Buteo buteo)",
    29: "Eurasian Kestrel (Falco tinnunculus)",
    30: "European Green Woodpecker (Picus viridis)",
}


def decode(hexstr):
    b = bytes.fromhex(hexstr.replace(" ", ""))
    if len(b) < 4:
        return None
    epoch = struct.unpack(">I", b[:4])[0]
    dets = []
    i = 4
    while i + 3 <= len(b):
        sid = struct.unpack(">H", b[i:i + 2])[0]
        conf = b[i + 2]
        dets.append((sid, SPECIES.get(sid, "Unknown sp. (id %d)" % sid), conf))
        i += 3
    return epoch, dets


def on_message(ws, message):
    if os.environ.get("LORIOT_DEBUG"):
        print("RAW:", message)
    try:
        m = json.loads(message)
    except ValueError:
        return
    if m.get("cmd") != "rx":
        return
    if str(m.get("port")) != str(FPORT):
        return
    data = m.get("data")
    if not data:
        return
    out = decode(data)
    if not out:
        return
    epoch, dets = out
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(epoch))
    for sid, name, conf in dets:
        print("%s  %-45s  %3d%%   (fcnt %s)" % (ts, name, conf, m.get("fcnt")))


def on_error(ws, err):
    print("[ws error] %s" % err)


def on_open(ws):
    print("[connected to LORIOT, waiting for uplinks on FPort %d ...]" % FPORT)


def main():
    if len(sys.argv) < 2:
        print('usage: python loriot_ws_decode.py "wss://eu1.loriot.io/app?token=<TOKEN>"')
        sys.exit(1)
    url = sys.argv[1]
    while True:
        try:
            ws = websocket.WebSocketApp(
                url, on_message=on_message, on_error=on_error, on_open=on_open
            )
            ws.run_forever(ping_interval=30, ping_timeout=10)
        except Exception as e:  # noqa: BLE001
            print("[loop error] %s" % e)
        print("[reconnecting in 5s ...]")
        time.sleep(5)


if __name__ == "__main__":
    main()
