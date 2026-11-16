#!/usr/bin/env python3
"""Simulated bird-detection sender for the UNO Q over CELLULAR (Waveshare A7670E).

Cellular sibling of bird_sender.py. It generates the *exact same* fake bird
detections (same SPECIES IDs, same 0.70..0.91 confidence) but instead of putting
a 3-byte LoRaWAN uplink on air through the LA66, it publishes a JSON telemetry
record to an MQTT broker through the A7670E Cat-1 modem.

This file is the APPLICATION: detection generation, the crash-durable disk spool,
batching, and the run loop. All modem/AT/MQTT mechanics live in the reusable
cellular interface layer **cell_modem.py** (the sibling of lora.py) — this module
just calls it. Keeping them split means a heartbeat, a bat sender, or a real
acoupi messenger can reuse cell_modem without copying AT-command handling.

ARCHITECTURE — offline-first, power-aware (mirrors lora.py's DiskSpoolSender):

    generate  ─every GEN_INTERVAL s→  spool/*.json   (atomic write, survives crash)
    flush     ─when >=BATCH_SIZE pending or FLUSH_MAX_AGE elapsed→
                  open modem → bring_up (SIM/signal/registration/data bearer)
                  → MQTT connect → publish each spooled record
                  → delete on confirmed +CMQTTPUB → disconnect → radio off

Generation NEVER blocks on the modem. If the modem is absent/busy, detections
keep spooling and the flush is skipped with a warning; the backlog drains once
the modem is back. Nothing is lost, nothing is double-sent (a file is removed
only after the modem confirms the publish).

    python3 cell_bird_sender.py                 # run in the foreground
    python3 cell_bird_sender.py --once          # spool one detection, flush, exit
    python3 cell_bird_sender.py --flush-only    # don't generate; just drain spool

Runtime deps for the flush path only: cell_modem.py (same dir) + pyserial. Both
are imported lazily, so the generator/spool half runs on a bare stdlib Python
with no pyserial and no modem present.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import random
import signal
import struct
import time

# --------------------------------------------------------------------------- #
# Config — override any of these via environment variables.
# --------------------------------------------------------------------------- #
def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)

# Modem serial. "auto" scans /dev/ttyUSB*/ttyACM* for the port that answers AT->OK
# (the A7670E exposes several ttyUSB nodes and the AT port number isn't stable, so
# auto-detect beats hard-coding). Pin via YEO_MODEM_PORT=/dev/ttyUSBx if you must.
PORT       = _env("YEO_MODEM_PORT", "auto")
BAUD       = int(_env("YEO_MODEM_BAUD", "115200"))

# Cellular / APN. LTE-only to dodge the 3G +CREG:3.
# Current SIM is KeySIM/Tele2 (APN "key", roams Vodafone-UK). The earlier
# Giffgaff/O2 SIM used "giffgaff.com". The APN must match the SIM or the modem
# reports CEREG 0,3 (data registration denied) despite good signal.
APN        = _env("YEO_APN", "key")
LTE_ONLY   = _env("YEO_LTE_ONLY", "1") == "1"

# MQTT broker. Defaults to a *test* topic so this never pollutes the live acoupi
# feed (yeo/unoq-bat/acoupi). Point at the CeTools broker used by the project.
BROKER_HOST = _env("YEO_MQTT_HOST", "mqtt.cetools.org")
BROKER_PORT = int(_env("YEO_MQTT_PORT", "1884"))
MQTT_USER   = _env("YEO_MQTT_USER", "")            # empty -> anonymous connect
MQTT_PASS   = _env("YEO_MQTT_PASS", "")
TOPIC       = _env("YEO_MQTT_TOPIC", "yeo/unoq-bird/cell")
CLIENT_ID   = _env("YEO_MQTT_CLIENTID", "unoq-dave-bird")
QOS         = int(_env("YEO_MQTT_QOS", "1"))
DEVICE_ID   = _env("YEO_DEVICE_ID", "dave")
SITE        = int(_env("YEO_SITE", "0"))

# Cadence / batching.
GEN_INTERVAL  = int(_env("YEO_GEN_INTERVAL", "180"))   # s between generated detections
BATCH_SIZE    = int(_env("YEO_BATCH_SIZE", "5"))       # flush once this many are queued
FLUSH_MAX_AGE = int(_env("YEO_FLUSH_MAX_AGE", "1800")) # ...or this many s since last flush
MAX_ATTEMPTS  = int(_env("YEO_MAX_ATTEMPTS", "5"))     # per-record before -> .failed
RADIO_OFF_BETWEEN = _env("YEO_RADIO_OFF", "1") == "1"  # CFUN=0 after each flush window
SPOOL_CAP     = int(_env("YEO_SPOOL_CAP", "1000"))     # keep at most this many queued;
                                                       # drop oldest beyond it (bounds
                                                       # disk if the modem is long absent)

SPOOL_DIR = os.path.expanduser(_env("YEO_SPOOL_DIR", "~/.yeo_bird_spool"))

# Simulated detection classes — identical to bird_sender.py / the MKR sketch.
SPECIES = (326, 599, 1018, 1352, 1783, 2441)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("cellbird")

_stop = False


def _handle_signal(signum, _frame):
    global _stop
    _stop = True
    log.info("signal %d received — finishing current step then exiting", signum)


# --------------------------------------------------------------------------- #
# Detection generation + durable spool (no modem needed for any of this).
# --------------------------------------------------------------------------- #
def build_detection(species: int, conf100: int) -> bytes:
    """species(uint16 BE) · confidence*100(uint8) -> 3 bytes.

    Byte-for-byte identical to bird_sender.py, so a cellular record and a LoRa
    uplink for the same detection carry the same `hex`."""
    if not (0 <= species <= 0xFFFF):
        raise ValueError("species must fit in uint16")
    if not (0 <= conf100 <= 0xFF):
        raise ValueError("conf100 must fit in uint8")
    return struct.pack(">HB", species, conf100)


def make_record() -> dict:
    """One fake detection as a JSON-friendly dict (superset of the LoRa payload)."""
    species = random.choice(SPECIES)
    conf100 = random.randint(70, 91)
    return {
        "device": DEVICE_ID,
        "site": SITE,
        "ts": int(time.time()),          # device clock; NS adds its own on LoRa path
        "species": species,
        "confidence": round(conf100 / 100.0, 2),
        "hex": build_detection(species, conf100).hex(),   # cross-checks with LoRa
        "transport": "cellular",
        "attempts": 0,
    }


def pending_spool() -> list[str]:
    try:
        return sorted(
            os.path.join(SPOOL_DIR, n)
            for n in os.listdir(SPOOL_DIR)
            if n.endswith(".json")
        )
    except FileNotFoundError:
        return []


def _trim_spool() -> None:
    """Drop oldest records so the spool never exceeds SPOOL_CAP (bounds disk when
    the modem has been unreachable a long time; keeps the freshest detections)."""
    paths = pending_spool()
    excess = len(paths) - SPOOL_CAP
    for path in paths[:max(0, excess)]:              # pending_spool() is oldest-first
        try:
            os.remove(path)
        except OSError:
            pass
    if excess > 0:
        log.warning("spool over cap (%d); dropped %d oldest record(s)", SPOOL_CAP, excess)


def spool_record(rec: dict) -> str:
    """Persist a detection atomically. Returns the spool path."""
    os.makedirs(SPOOL_DIR, exist_ok=True)
    # time_ns keeps files oldest-first; pid avoids collisions across processes.
    name = f"{time.time_ns():020d}-{os.getpid()}.json"
    path = os.path.join(SPOOL_DIR, name)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(rec, f)
    os.replace(tmp, path)                # atomic publish into the spool
    _trim_spool()                        # bound the queue if modem's been away
    return path


def _fail_file(path: str, why: str) -> None:
    try:
        os.replace(path, path + ".failed")
        log.error("giving up on %s (%s)", os.path.basename(path), why)
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# Flush: drain the spool over one cellular window (delegates to cell_modem).
# --------------------------------------------------------------------------- #
def flush_spool() -> int:
    """Bring the modem up, publish every spooled record, tear down. Returns #sent.

    Never raises on 'modem absent/busy' — logged, returns 0, caller keeps
    generating. A file is deleted only after a confirmed publish; on repeated
    failure a record is retried up to MAX_ATTEMPTS then moved aside to *.failed."""
    paths = pending_spool()
    if not paths:
        return 0

    import cell_modem  # lazy: keeps the generator half stdlib-only

    modem = cell_modem.CellModem(port=PORT, baud=BAUD, apn=APN, lte_only=LTE_ONLY)
    try:
        modem.open()
    except cell_modem.ModemNotPresent as e:          # absent or busy (e.g. ModemManager)
        log.warning("flush skipped, %d record(s) still queued: %s", len(paths), e)
        return 0

    sent = 0
    try:
        modem.bring_up()
        modem.mqtt_connect(BROKER_HOST, BROKER_PORT, CLIENT_ID,
                           username=MQTT_USER, password=MQTT_PASS)
        for path in paths:
            if _stop:
                break
            try:
                with open(path) as f:
                    rec = json.load(f)
            except (OSError, ValueError):
                _fail_file(path, "unreadable")
                continue
            try:
                modem.mqtt_publish(TOPIC, json.dumps(rec), qos=QOS)
                os.remove(path)                      # confirmed +CMQTTPUB -> done
                sent += 1
                log.info("published %s species=%s conf=%.2f",
                         os.path.basename(path), rec.get("species"), rec.get("confidence", 0))
            except cell_modem.ModemError as e:
                rec["attempts"] = int(rec.get("attempts", 0)) + 1
                log.warning("publish failed (%s) attempt %d/%d for %s",
                            e, rec["attempts"], MAX_ATTEMPTS, os.path.basename(path))
                if rec["attempts"] >= MAX_ATTEMPTS:
                    _fail_file(path, str(e))
                else:
                    tmp = path + ".tmp"
                    with open(tmp, "w") as f:
                        json.dump(rec, f)
                    os.replace(tmp, path)            # persist bumped attempt count
                # A publish failure often means the link dropped — stop this window,
                # keep the rest queued for the next flush rather than hammering.
                break
    except cell_modem.ModemError as e:
        log.error("flush aborted (%d record[s] kept queued): %s", len(pending_spool()), e)
    finally:
        try:
            modem.mqtt_disconnect()
            if RADIO_OFF_BETWEEN:
                modem.radio_off()
        finally:
            modem.close()
    if sent:
        log.info("flush window done: %d sent, %d still queued", sent, len(pending_spool()))
    return sent


# --------------------------------------------------------------------------- #
# Main loop.
# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser(description="Cellular fake-bird-detection sender (UNO Q + A7670E)")
    ap.add_argument("--once", action="store_true",
                    help="spool one detection, attempt a flush, then exit")
    ap.add_argument("--flush-only", action="store_true",
                    help="do not generate; just drain the existing spool and exit")
    args = ap.parse_args()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    log.info("cell bird sender: port=%s broker=%s:%s topic=%s gen=%ds batch=%d",
             PORT, BROKER_HOST, BROKER_PORT, TOPIC, GEN_INTERVAL, BATCH_SIZE)
    log.info("spool dir: %s (%d record[s] already queued)", SPOOL_DIR, len(pending_spool()))

    if args.flush_only:
        flush_spool()
        return

    if args.once:
        spool_record(make_record())
        flush_spool()
        return

    last_flush = time.monotonic()
    while not _stop:
        spool_record(make_record())
        queued = len(pending_spool())
        age = time.monotonic() - last_flush
        if queued >= BATCH_SIZE or age >= FLUSH_MAX_AGE:
            flush_spool()                            # graceful if modem absent
            last_flush = time.monotonic()
        # Sleep in short slices so SIGTERM is responsive.
        slept = 0.0
        while slept < GEN_INTERVAL and not _stop:
            time.sleep(min(2.0, GEN_INTERVAL - slept))
            slept += 2.0
    log.info("stopped; %d record(s) remain queued in %s", len(pending_spool()), SPOOL_DIR)


if __name__ == "__main__":
    main()
