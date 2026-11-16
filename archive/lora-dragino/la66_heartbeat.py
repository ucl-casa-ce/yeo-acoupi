#!/usr/bin/env python3
"""LA66 heartbeat sender (single-shot) — run from a systemd timer or cron.

Sends one small LoRaWAN uplink via the LA66 socket bridge
(127.0.0.1:7500, AT+SENDB). Intended as a periodic "I'm alive" frame.

Payload (FPort 1, "heartbeat"):
  bytes 0-3 : uint32 big-endian unix epoch (UTC)
  byte  4   : uint8 sequence counter (wraps at 256), persisted across runs

TTN uplink decoder (paste into Payload formatters → Uplink) for FPort 1:
  if (fPort === 1) {
    var epoch = (bytes[0]<<24)|(bytes[1]<<16)|(bytes[2]<<8)|bytes[3];
    return { data: { type:"heartbeat",
                     time:new Date(epoch*1000).toISOString(),
                     seq: bytes[4] } };
  }

Idempotent + safe: read-nothing, just fires AT+SENDB once and exits.
Configure once joined; do NOT enable the timer until the device shows JOINED in TTN.
"""
import os
import socket
import struct
import time

LA66 = ("127.0.0.1", 7500)
FPORT = int(os.environ.get("HB_FPORT", "1"))
SEQ_FILE = os.environ.get("HB_SEQ_FILE", "/home/arduino/.la66_hb_seq")
LOGFILE = os.environ.get("HB_LOG", "/home/arduino/la66_heartbeat.log")


def log(msg):
    line = "%s %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    try:
        with open(LOGFILE, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def next_seq():
    seq = 0
    try:
        with open(SEQ_FILE) as f:
            seq = (int(f.read().strip()) + 1) & 0xFF
    except (OSError, ValueError):
        seq = 0
    try:
        with open(SEQ_FILE, "w") as f:
            f.write(str(seq))
    except OSError:
        pass
    return seq


def main():
    seq = next_seq()
    buf = bytearray(struct.pack(">I", int(time.time())))
    buf.append(seq)
    cmd = "AT+SENDB=0,%d,%d,%s\r\n" % (FPORT, len(buf), buf.hex().upper())
    try:
        s = socket.create_connection(LA66, timeout=5)
        try:
            s.sendall(cmd.encode())
            time.sleep(0.5)
        finally:
            s.close()
        log("heartbeat TX seq=%d: %s" % (seq, cmd.strip()))
    except OSError as e:
        log("heartbeat send FAILED (bridge down?): %s" % e)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
