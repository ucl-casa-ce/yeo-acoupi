#!/usr/bin/env python3
"""Simulated bird-detection sender for the UNO Q + Dragino LA66.

LA66 / UNO-Q port of the MKR WAN 1310 "bird detection node" sketch. Instead of
MKRWAN + OTAA on an MKR board, this runs on the UNO Q's *Linux* side and puts the
same uplink on air through the stacked LA66 shield (raw-socket path via lora.py).
Same idea as tick_sender.py, but the payload is a simulated detection rather than
an incrementing tick.

Every INTERVAL seconds it emits one simulated detection:

  Payload (3 bytes, FPort 2) — byte-for-byte identical to the MKR sketch:
    byte 0-1 : species ID        (uint16, big-endian)   e.g. 2441
    byte 2   : confidence x 100  (uint8, 70..91)         -> /100 downstream = 0.70..0.91
  The timestamp is added server-side by the network server (uplink "time" field).

HANDOVER NOTE (2026-07-18): the downstream described below is out of date. The
current path is LA66 -> MultiTech Conduit built-in Network Server -> local
mosquitto -> MQTT bridge -> mqtt.cetools.org. See UNO_Q_LA66_SETUP.md §11 and
the gateway guide in 3-gateway/. The uplink arrives on cetools as
`student/yeo/lora/<DevEUI>/up` with `data` as base64 — field decoding happens
downstream of cetools, not on the gateway, so the TTN decodeUplink() at the
bottom of this file is no longer wired in anywhere. The 3-byte layout it
documents is still correct and is the reference for whatever does the decoding.
The sending code itself is network-agnostic and needs no change.

DEVICE SIDE ONLY. Downstream (HISTORICAL, TTN era): LA66 -> gateway -> TTN ->
ttn_mqtt_bridge.py -> MQTT. Two things must match this 3-byte payload rather than
the 7-byte magic/tag/tick/site beacon:
  * the TTN uplink decoder  -> use the decodeUplink() at the bottom of this file;
  * ttn_mqtt_bridge.py       -> it reads `tick`; adapt it to read species/confidence
                                (or just republish decoded_payload as JSON).

The board's WiFi does NOT need to be up for this to work: the uplink is RF
(LA66 -> gateway), independent of the board's internet. WiFi only matters for
SSH and for running the MQTT bridge (which lives on a PC/server, not the board).

    python3 bird_sender.py            # run in the foreground
    # or install as a systemd service like yeo-lora-ticker (see LA66 runbook)

Requires lora.py in the same directory (stdlib-only, no pip).
"""
import logging
import random
import struct
import time

# PORTED FOR HANDOVER: was `import lora` (the superseded driver in
# unoq/lora/lora.py). lorawan.py exposes the same configure(dr=,
# full_channel_plan=) and send(payload, fport=, confirm=) signatures, so
# this alias is the only change. Untested against hardware since the swap.
import lorawan as lora

# ---- config -----------------------------------------------------------------
INTERVAL = 180          # seconds between uplinks. NOTE: the MKR sketch used 60 s,
                        # but at DR0/SF12 the EU868 1% duty cycle needs ~131 s
                        # off-air, so 60 s is not legal at SF12. 180 s matches the
                        # proven tick_sender cadence; lora.py also paces internally
                        # as a backstop. You can go to ~60 s only at DR5/SF7.
DR = 0                  # 0 = SF12 (max range); 5 = SF7 (least airtime)
FPORT = 2               # matches the TTN device / decoder in the runbook
CONFIRM = False         # unconfirmed uplink, like the MKR sketch's endPacket(false)

# Simulated detection classes — same species IDs as the MKR WAN 1310 sketch.
SPECIES = (326, 599, 1018, 1352, 1783, 2441)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bird")


def build_detection(species: int, conf100: int) -> bytes:
    """species(uint16 BE) · confidence*100(uint8)  ->  3 bytes.

    Byte-for-byte identical to the MKR WAN 1310 sketch's payload:
        payload[0] = (species >> 8) & 0xFF
        payload[1] =  species       & 0xFF
        payload[2] =  conf100
    """
    if not (0 <= species <= 0xFFFF):
        raise ValueError("species must fit in uint16")
    if not (0 <= conf100 <= 0xFF):
        raise ValueError("conf100 must fit in uint8")
    return struct.pack(">HB", species, conf100)


def main() -> None:
    link = lora.get_link()
    try:
        link.configure(dr=DR, full_channel_plan=True)    # SF + full EU868 plan
    except lora.LoRaError as e:
        log.warning("configure failed (continuing anyway): %s", e)

    log.info("bird sender starting, interval=%ds, DR=%d, fport=%d, confirm=%s",
             INTERVAL, DR, FPORT, CONFIRM)
    sent = 0
    while True:
        # Semi-random simulated detection (mirrors the MKR sketch's loop()).
        species = random.choice(SPECIES)                 # one of the 6 species IDs
        conf100 = random.randint(70, 91)                 # 70..91 -> 0.70..0.91
        payload = build_detection(species, conf100)
        try:
            res = link.send(payload, fport=FPORT, confirm=CONFIRM)  # waits for txDone
            sent += 1
            log.info("TX ok #%d species=%d confidence=%.2f: %s",
                     sent, species, conf100 / 100.0, res)
        except lora.LoRaError as e:
            log.error("TX failed species=%d (retry next interval): %s", species, e)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()


# ----------------------------------------------------------------------------- #
# TTN uplink decoder for THIS 3-byte payload.
# Paste into: TTN Console -> your application -> your device (or app) ->
#             Payload formatters -> Uplink -> "Custom Javascript formatter".
# Replaces the 7-byte magic/tag/tick/site decoder in UNO_Q_LA66_SETUP.md §8d.
# ----------------------------------------------------------------------------- #
#   function decodeUplink(input) {
#     var b = input.bytes;
#     if (b.length < 3) return { errors: ["payload too short (need 3 bytes)"] };
#     return {
#       data: {
#         species:    (b[0] << 8) | b[1],   // uint16 big-endian, e.g. 2441
#         confidence: b[2] / 100.0          // 0.70 .. 0.91
#       }
#     };
#   }
