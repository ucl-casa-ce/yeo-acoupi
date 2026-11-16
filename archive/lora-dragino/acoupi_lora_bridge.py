#!/usr/bin/env python3
"""acoupi -> LoRa bridge sidecar (stdlib only).

Listens for acoupi's HTTP-messenger POSTs on 127.0.0.1:8000, pulls out
(species, confidence) detections, encodes a compact LoRaWAN payload, and
sends it via the LA66 socket bridge (127.0.0.1:7500, AT+SENDB).

Payload format (matches lora-dragino/ttn-payload-decoder.md):
  bytes 0-3 : uint32 big-endian unix epoch
  then per detection, 3 bytes: uint16 species_id + uint8 confidence%

v1 deliberately LOGS every POST body so we can see acoupi's real message
schema, then does a best-effort detection extraction + LoRa send.
"""
import json
import socket
import struct
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

LISTEN = ("127.0.0.1", 8000)
LA66 = ("127.0.0.1", 7500)
FPORT = 2
MIN_SEND_INTERVAL = 30.0          # seconds between uplinks (EU868 duty-cycle guard)
CONF_THRESHOLD = 0.7              # belt-and-braces; acoupi already gates at 0.7
LOGFILE = "/home/arduino/acoupi_lora_bridge.log"

# BirdNET scientific name -> stable 2-byte id. KEEP IN SYNC with the LORIOT decoder
# (lora-dragino/loriot-payload-decoder.md). Unknown species -> deterministic id >=1000.
SPECIES_LUT = {
    "Columba palumbus": 1,          # Common Wood-Pigeon
    "Corvus corone": 2,             # Carrion Crow
    "Corvus brachyrhynchos": 3,     # American Crow
    "Streptopelia decaocto": 4,     # Eurasian Collared-Dove
    "Turdus merula": 5,             # Eurasian Blackbird
    "Erithacus rubecula": 6,        # European Robin
    "Cyanistes caeruleus": 7,       # Eurasian Blue Tit
    "Parus major": 8,               # Great Tit
    "Passer domesticus": 9,         # House Sparrow
    "Fringilla coelebs": 10,        # Common Chaffinch
    "Sturnus vulgaris": 11,         # European Starling
    "Pica pica": 12,                # Eurasian Magpie
    "Troglodytes troglodytes": 13,  # Eurasian Wren
    "Prunella modularis": 14,       # Dunnock
    "Carduelis carduelis": 15,      # European Goldfinch
    "Chloris chloris": 16,          # European Greenfinch
    "Phylloscopus collybita": 17,   # Common Chiffchaff
    "Sylvia atricapilla": 18,       # Eurasian Blackcap
    "Turdus philomelos": 19,        # Song Thrush
    "Aegithalos caudatus": 20,      # Long-tailed Tit
    "Corvus monedula": 21,          # Eurasian Jackdaw
    "Corvus frugilegus": 22,        # Rook
    "Garrulus glandarius": 23,      # Eurasian Jay
    "Apus apus": 24,                # Common Swift
    "Hirundo rustica": 25,          # Barn Swallow
    "Larus argentatus": 26,         # European Herring Gull
    "Anas platyrhynchos": 27,       # Mallard
    "Buteo buteo": 28,              # Common Buzzard
    "Falco tinnunculus": 29,        # Eurasian Kestrel
    "Picus viridis": 30,            # European Green Woodpecker
}

_last_send = 0.0


def log(msg):
    line = "%s %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    try:
        with open(LOGFILE, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def species_to_id(name):
    if name in SPECIES_LUT:
        return SPECIES_LUT[name]
    # unknown species -> deterministic id in a non-colliding range (>=1000)
    return 1000 + (sum(name.encode("utf-8")) * 131) % 60000


def extract_detections(payload, out):
    """Parse acoupi BirdNET ModelOutput JSON:
    payload['detections'][].detection_score  +  tags[].tag.value (species name).
    """
    for det in payload.get("detections") or []:
        score = det.get("detection_score")
        if score is None:
            continue
        name = "unknown"
        for t in det.get("tags") or []:
            tg = t.get("tag") or {}
            if tg.get("value"):
                name = tg["value"]
                break
        try:
            out.append((str(name), float(score)))
        except (TypeError, ValueError):
            pass


def send_lora(dets):
    ts = int(time.time())
    buf = bytearray(struct.pack(">I", ts))
    for name, conf in dets:
        pct = max(0, min(100, int(round(conf * 100))))
        buf += struct.pack(">HB", species_to_id(name), pct)
    cmd = "AT+SENDB=0,%d,%d,%s\r\n" % (FPORT, len(buf), buf.hex().upper())
    s = socket.create_connection(LA66, timeout=5)
    try:
        s.sendall(cmd.encode())
        time.sleep(0.5)
    finally:
        s.close()
    log("LoRa TX (%d det): %s" % (len(dets), cmd.strip()))


class Handler(BaseHTTPRequestHandler):
    def _handle(self):
        global _last_send
        n = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(n) if n else b""
        if raw:
            log("%s %s body=%s" % (self.command, self.path,
                                   raw.decode("utf-8", "replace")))
        # respond 200 to every method (acoupi's health check probes with HEAD/GET)
        body = b'{"status":"ok"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
        # only message bodies carry detections
        if not raw:
            return
        try:
            payload = json.loads(raw)
        except ValueError:
            log("  (body was not JSON; logged only)")
            return
        dets = []
        extract_detections(payload, dets)
        dets = [(nm, c) for (nm, c) in dets if c >= CONF_THRESHOLD]
        if not dets:
            log("  no detections >= %.2f parsed" % CONF_THRESHOLD)
            return
        now = time.time()
        if now - _last_send < MIN_SEND_INTERVAL:
            log("  duty-cycle guard: %.0fs since last send, skipping" % (now - _last_send))
            return
        try:
            send_lora(dets)
            _last_send = now
        except OSError as e:
            log("  LoRa send failed: %s" % e)

    do_GET = _handle
    do_HEAD = _handle
    do_POST = _handle
    do_PUT = _handle
    do_PATCH = _handle
    do_OPTIONS = _handle
    do_DELETE = _handle

    def log_message(self, *a):
        pass                                  # silence default stderr logging


if __name__ == "__main__":
    log("acoupi-lora-bridge listening on http://%s:%d" % LISTEN)
    HTTPServer(LISTEN, Handler).serve_forever()
