#!/usr/bin/env python3
"""Configure the LA66 for TTN OTAA join (blind config) and reboot to join.

Sends the OTAA profile per lora-dragino/loriot-setup.md §7a, then ATZ.
Per that doc: config is sent "blind" (the bridge's TX/RX are independent and
AT+CFG read-back garbles during transmit) — verify the JOIN at the TTN console.

Credentials come from the environment so keys never live in the repo:
  TTN_DEUI    8-byte DevEUI   (MSB-first, no spaces) e.g. A84041000181XXXX
  TTN_APPEUI  8-byte JoinEUI  (a.k.a. AppEUI; TTN often all-zeros)
  TTN_APPKEY  16-byte AppKey  (MSB-first, no spaces)

Region is fixed to EU868 expectations (ADR on, all channels). This script
NEVER sends AT+FDR (factory reset) — that would erase keys + counters.

Usage (on the board):
  TTN_DEUI=... TTN_APPEUI=... TTN_APPKEY=... python3 la66_join_ttn.py
Add --dry-run to print the command sequence without sending.
"""
import os
import socket
import sys
import time

LA66 = ("127.0.0.1", 7500)


def hexlen(s):
    s = (s or "").strip().replace(" ", "").replace("0x", "")
    return s, len(s)


def build_sequence():
    deui, ld = hexlen(os.environ.get("TTN_DEUI"))
    appeui, la = hexlen(os.environ.get("TTN_APPEUI"))
    appkey, lk = hexlen(os.environ.get("TTN_APPKEY"))

    errs = []
    if ld != 16:
        errs.append("TTN_DEUI must be 16 hex chars (8 bytes); got %d" % ld)
    if la != 16:
        errs.append("TTN_APPEUI must be 16 hex chars (8 bytes); got %d" % la)
    if lk != 32:
        errs.append("TTN_APPKEY must be 32 hex chars (16 bytes); got %d" % lk)
    if errs:
        for e in errs:
            print("ERROR:", e, file=sys.stderr)
        raise SystemExit(2)

    # Order per loriot-setup.md §7a. ATZ last → triggers JoinRequest.
    return [
        "AT+NJM=1",            # OTAA
        "AT+DEUI=%s" % deui,
        "AT+APPEUI=%s" % appeui,
        "AT+APPKEY=%s" % appkey,
        "AT+ADR=1",            # ADR on (real multi-channel gateway)
        "AT+CHS=0",            # use all band channels
        "AT+CLASS=A",
        "ATZ",                 # reboot -> join
    ]


def send_blind(cmds, settle=0.8):
    s = socket.create_connection(LA66, timeout=5)
    try:
        for c in cmds:
            print(">>>", c.replace(os.environ.get("TTN_APPKEY", "@@@@"), "<APPKEY>"))
            s.sendall((c + "\r\n").encode())
            time.sleep(settle)
    finally:
        s.close()


if __name__ == "__main__":
    cmds = build_sequence()
    if "--dry-run" in sys.argv:
        for c in cmds:
            redacted = c
            if c.startswith("AT+APPKEY="):
                redacted = "AT+APPKEY=<APPKEY 32 hex>"
            print(redacted)
        raise SystemExit(0)
    print("[*] Sending OTAA config blind to LA66, then ATZ to join...")
    send_blind(cmds)
    print("[*] Done. Watch the TTN console live data for JOIN + first uplink.")
    print("[*] (Read-back via AT+CFG is unreliable during TX — verify at TTN.)")
