"""Autonomous LoRaWAN tick beacon for UNO Q + Dragino LA66.

Runs as the Arduino App's Python side. Drives the LA66 over the Bridge RPC
methods provided by la66_bridge.ino (la66_send / la66_drain) — no raw :7500 socket,
so it does not race the framework.

Pinned to SINGLE-CHANNEL 868.1 MHz / SF12 to match the single-channel ESP
gateway that forwards Semtech-UDP to LORIOT (eu1.loriot.io:1780). Sends an
unconfirmed uplink (single-channel gateways can't return the downlink ACK).

Payload = 7 bytes:  magic(0xAC) . tag(0x51='Q') . tick(uint32 LE) . site(1)
e.g. tick=1 -> AC510100000000  (FPort 2). The tick persists across reboots.
"""
from arduino.app_utils import *
import os
import struct
import time

INTERVAL = 180                                  # seconds between uplinks (3 min)
SITE = 0                                        # site byte in the beacon
STATE_FILE = os.path.expanduser("~/.yeo_tick")  # persisted tick counter
FPORT = 2

# LA66 config for the single-channel -> LORIOT path.
#   AT        : warm-up (first cmd after boot often returns AT_ERROR)
#   AT+ADR=0  : disable adaptive DR (required so a fixed DR/channel sticks)
#   AT+CHS=.. : pin to single channel 868.1 MHz (gateway listens only here)
#   AT+DR=0   : DR0 = SF12 (matches the gateway; max range)
# NOTE: AT+CHS only takes effect after a module reset (ATZ) — setting it alone
# leaves the LA66 hopping the full EU868 plan. So we set it, then ATZ once, so
# the module boots into single-channel 868.1 mode.
CONFIG_CMDS = ["AT", "AT+ADR=0", "AT+DR=0", "AT+CHS=868100000"]
VERIFY_CMDS = ["AT+ADR=?", "AT+CHS=?", "AT+DR=?"]


def at(cmd, wait=2.5):
    """Send one AT line and return the LA66's reply text (must run on App loop thread)."""
    Bridge.call("la66_send", cmd, timeout=8)
    time.sleep(wait)
    return Bridge.call("la66_drain", 0, timeout=8)


def load_tick():
    try:
        return int(open(STATE_FILE).read().strip())
    except (OSError, ValueError):
        return 0


def save_tick(t):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        f.write(str(t))
    os.replace(tmp, STATE_FILE)                 # atomic


def build_payload(tick):
    return b"\xac\x51" + struct.pack("<I", tick) + bytes([SITE])


_state = {"configured": False, "tick": load_tick(), "next_due": 0.0}


def loop():
    st = _state
    if not st["configured"]:
        time.sleep(2.0)                          # let the STM32/LA66 finish booting
        Bridge.call("la66_drain", 0, timeout=8)  # clear boot bytes
        for c in CONFIG_CMDS:
            print("cfg %-16s -> %r" % (c, at(c)), flush=True)
        # Reset once so AT+CHS single-channel mode takes effect.
        print("reset ATZ        -> %r" % at("ATZ", wait=6.0), flush=True)
        at("AT", wait=2.0)                       # warm-up after reboot
        Bridge.call("la66_drain", 0, timeout=8)  # clear post-reset boot bytes
        for c in VERIFY_CMDS:
            print("chk %-16s -> %r" % (c, at(c, wait=1.5)), flush=True)
        st["configured"] = True
        st["next_due"] = 0.0                     # send the first tick immediately
        print("configured; ticking every %ds at tick=%d" % (INTERVAL, st["tick"]),
              flush=True)

    now = time.time()
    if now < st["next_due"]:
        time.sleep(1.0)
        return

    nt = st["tick"] + 1
    hexpl = build_payload(nt).hex().upper()
    cmd = "AT+SENDB=00,%02X,7,%s" % (FPORT, hexpl)
    reply = at(cmd, wait=9)                       # give it time to reach txDone
    ok = ("txDone" in reply) or ("OK" in reply)
    print("tick %d %s cmd=%s reply=%r" % (nt, "OK" if ok else "FAIL", cmd, reply),
          flush=True)
    if ok:
        st["tick"] = nt
        save_tick(nt)
    st["next_due"] = time.time() + INTERVAL


App.run(user_loop=loop)
