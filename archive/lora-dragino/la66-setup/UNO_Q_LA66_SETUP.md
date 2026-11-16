# UNO Q + Dragino LA66 — Setup Runbook

How to drive a **Dragino LA66 LoRaWAN Shield** from the **Linux side** of an
**Arduino UNO Q**, and put real LoRaWAN frames on air.

**Scope / status:** this covers everything up to and including the LA66
**transmitting correct LoRaWAN uplinks on air** (868.1 MHz, SF12), driven by
Python on the UNO Q's Linux side. The gateway → network-server half is **not**
covered here (it did not work in our setup and is deliberately excluded).

**What's verified:** Linux → Bridge → STM32 → LA66 → RF transmit, end to end on
the device. **Not verified:** frames arriving at a network server.

---

## 0. The one thing you must understand first

The LA66 shield talks over a plain **2-wire 3.3 V UART** on Arduino pins
**D0/D1**. On the UNO Q, **D0/D1 are wired to the STM32 MCU, _not_ to Linux** —
in fact *every* Arduino header pin is the STM32's. So Linux cannot open the
LA66's serial port directly.

The path that works is the UNO Q's built-in **Bridge**:

```
Linux Python  ──Bridge RPC──►  STM32 sketch  ──Serial1 (D0/D1) @9600──►  LA66  ──RF──►
(builds AT cmds)  (arduino-router)  (relays bytes)                      (LoRaWAN radio)
```

- The **Bridge/router** runs on the STM32's `lpuart1` (internal) — a *different*
  UART from D0/D1, so it doesn't clash with the shield.
- In the sketch, the LA66 is on **`Serial1`** (= STM32 `usart1` = D0/D1), at
  **9600 8N1**. (`Serial1.begin(9600)` just works, despite `usart1` nominally
  being the Zephyr console.)
- You do **not** need a USB-UART adapter, and you do **not** route anything
  through a separate cable — the STM32 is the bridge.

---

## 1. Hardware

- **Arduino UNO Q** (Qualcomm QRB2210 Linux + STM32U585 MCU).
- **Dragino LA66 LoRaWAN Shield** (EU868 variant), stacked on the UNO Q headers.
  - **Jumpers:** LA66 `TXD → D0`, LA66 `RXD → D1` (crossover; D0=RX, D1=TX).
  - **`JP6`** fitted (powers the LA66 module).
  - Antenna attached **before** transmitting.
- **Power: a real 5 V / 3 A supply** into the USB-C.
  - ⚠️ **The board browns out on a normal PC USB port** once the shield is
    stacked — it powers the LED but never finishes booting Linux. Symptom we hit:
    solid green on the UNO Q, LA66 LED flickers on for ~2 s then dies. Use a
    proper 5 V/3 A brick (or a powered hub), not a laptop port.

---

## 2. UNO Q OS — only if the board is in a bad state (EDL recovery)

Skip if the board already boots and you can reach it. If it's bricked/unknown:

1. **Power off** the board.
2. On the **JCTL header**, short the **two pins furthest from the USB-C
   connector** with a jumper/shunt.
3. Connect USB-C to a computer. It enumerates as
   `lsusb → 05c6:9008 Qualcomm … (QDL mode)`.
4. Download the **Arduino Flasher CLI** (`arduino-flasher-cli`) from
   arduino.cc/en/software. Flash (needs root for the `05c6:9008` device):
   ```bash
   sudo ./arduino-flasher-cli flash latest
   ```
   Answer `y` to download the image and `y` to erase. Ends with
   `partition 0 is now bootable`.
5. **Remove the JCTL jumper**, replug USB-C → it boots factory-fresh and runs
   first-setup (password, **WiFi**, device name).

> In EDL mode the board draws almost no power, so it enumerates even if it was
> too under-powered to boot normally — handy for diagnosis.

---

## 3. Get a Linux shell on the UNO Q

Default user is **`arduino`**. Two ways in:

- **WiFi + SSH** (after first-setup joined it to WiFi):
  ```bash
  ssh arduino@<boardname>.local     # e.g. ssh arduino@unoq-2.local
  # or by IP if mDNS is flaky:  ssh arduino@192.168.x.y
  ```
- **USB + adb** (data-capable USB-C cable, board shows as `adb devices`):
  ```bash
  adb shell
  # enable SSH without the desktop App Lab:
  arduino-app-cli system network-mode enable
  ```

**Passwordless SSH** (push your host key over adb, no board password needed):
```bash
adb push ~/.ssh/id_ed25519.pub /tmp/k.pub
adb shell 'mkdir -p ~/.ssh && cat /tmp/k.pub >> ~/.ssh/authorized_keys && chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys'
```

The board is stock **Debian 13**; `arduino-app-cli` is preinstalled; user
`arduino` is already in `dialout`/`gpiod`. Nothing else to install for this.

---

## 4. Create the Arduino "App" (sketch + Python)

An App = a C++ **sketch** (runs on the STM32) + a **Python** program (runs on
Linux), linked by the Bridge. Create the scaffold once:

```bash
arduino-app-cli app new la66 -d "LA66 LoRaWAN bridge"
# creates ~/ArduinoApps/la66/{sketch/sketch.ino, python/main.py, app.yaml, ...}
```

### 4a. `~/ArduinoApps/la66/sketch/sketch.ino`

This sketch exposes two functions to Linux: `la66_send(cmd)` writes an AT line
to the LA66; `la66_drain()` returns everything the LA66 has said since last call.

```cpp
#include "Arduino_RouterBridge.h"

// LA66 shield UART is on D0/D1 = STM32 USART1 = Serial1.
String rxbuf = "";

String la66_drain(int _unused) {   // return + clear buffered LA66 output
  String s = rxbuf;
  rxbuf = "";
  return s;
}

int la66_send(String cmd) {        // write one AT line to the LA66
  Serial1.print(cmd);
  Serial1.print("\r\n");
  return cmd.length();
}

void setup() {
  Bridge.begin();
  Serial1.begin(9600);             // LA66 AT interface = 9600 8N1
  Bridge.provide("la66_drain", la66_drain);
  Bridge.provide("la66_send", la66_send);
}

void loop() {
  while (Serial1.available()) {
    char c = Serial1.read();
    if (rxbuf.length() < 2000) rxbuf += c;
  }
}
```

### 4b. `~/ArduinoApps/la66/python/main.py`

Reusable driver: runs a list of AT commands and prints each reply to the app
log. Edit `COMMANDS` for whatever you need.

```python
from arduino.app_utils import *
import time

COMMANDS = [
    "AT",                                 # warm-up (see gotcha below)
    "AT+CFG",                             # dump config: DevEUI/DevAddr/keys/band
    # "AT+DR=0",                          # DR0 = SF12 (match a SF12 gateway)
    # "AT+SENDB=00,02,7,AC510100000000",  # unconfirmed uplink, FPort 2, 7 bytes
]

def at(cmd, wait=2.5):
    Bridge.call("la66_send", cmd, timeout=8)
    time.sleep(wait)
    return Bridge.call("la66_drain", 0, timeout=8)

done = False
def loop():
    global done
    if done:
        time.sleep(10)
        return
    time.sleep(1.0)
    Bridge.call("la66_drain", 0, timeout=8)   # clear boot bytes
    for c in COMMANDS:
        print("=== %s ===" % c, flush=True)
        try:
            print(at(c), flush=True)
        except Exception as e:
            print("ERROR: %r" % (e,), flush=True)
    done = True

App.run(user_loop=loop)
```

### 4c. Build, flash the sketch, and run — all headless

```bash
arduino-app-cli app start user:la66      # compiles sketch, flashes STM32, runs Python
arduino-app-cli app logs  user:la66      # watch output (the AT replies)

# after editing files:
arduino-app-cli app stop  user:la66
arduino-app-cli app start user:la66      # 'start' reflashes the sketch if it changed
```

The Python side runs in a container named `la66-main-1`; its `print()` output
shows in `app logs`.

---

## 5. Driving the LA66 (AT reference for what we used)

The LA66 we had reported: **`AT+VER=EU868 v1.3`**, **`AT+NJM=0` (ABP)**, and
**`AT+CHS=868100000`** (single-channel mode pinned to 868.1 MHz).

- **Dump everything (incl. DevEUI/DevAddr/keys):** `AT+CFG`
- **Set spreading factor via data rate** (EU868): `AT+DR=0` → **SF12**,
  `AT+DR=5` → SF7. *The module defaulted to DR5/SF7 for us; force `AT+DR=0` if
  you need SF12.* (Quirk: `AT+DR=?` may still read `5` while the actual TX log
  shows `DR 0` — trust the `TX on freq … at DR 0` line.)
- **Send an uplink** (binary/hex):
  ```
  AT+SENDB=<confirm>,<FPort>,<len_bytes>,<hexdata>
  e.g.  AT+SENDB=00,02,7,AC510100000000
        confirm=00 (unconfirmed)  FPort=02  len=7  data=7 bytes
  ```
  Use **`confirm=00` (unconfirmed)** with a single-channel gateway (it can't
  send the downlink ACK a confirmed uplink needs).

A successful transmit looks like this in the log:
```
***** UpLinkCounter= 1 *****
TX on freq 868.100 MHz at DR 0
OK
txDone
RX on freq 868.100 MHz at DR 0   → rxTimeout   (expected: no downlink)
RX on freq 869.525 MHz at DR 0   → rxTimeout
```

The `AC510100000000` payload is a tagged 7-byte beacon:
`AC` (magic) · `51`=`'Q'` (device tag) · `01 00 00 00` (tick, u32 LE) · `00`
(site) — decodable by the existing `common.py` ingest.

---

## 6. Gotchas we hit (so you don't)

| Symptom | Cause | Fix |
|---|---|---|
| Board never appears on USB/WiFi; solid green LED, LA66 LED dies after ~2 s | Under-power (PC USB port can't run board + shield) | Use a real **5 V/3 A** supply |
| `AT+CFG` etc. return empty from a bare 2nd process / `docker exec` | Bridge replies only go to the **App's own process** | Do all `Bridge.call`s inside the App's `main.py` (under `App.run`) |
| `Bridge.call` times out from a worker/socket thread | Bridge only works from the **App loop thread** | Marshal commands into `loop()`; don't call Bridge from other threads |
| `Bridge.call` times out after several app restarts | STM32 `provide()`s registered only in `setup()`; a restart that doesn't reflash leaves them **stale** | Force a sketch reflash (change a byte in `sketch.ino`) → `stop`/`start` |
| **First** AT command after boot returns `AT_ERROR` | STM32 console emits boot bytes on D0/D1 that spoil the first command | Send a throwaway `AT` (+ drain) first, then your real command |
| Uplink transmits but at wrong SF | LA66 defaulted to **DR5/SF7** | `AT+DR=0` before sending (SF12) |
| `unoq-2.local` won't resolve | mDNS hiccup | Use the board's IP instead |

---

## 7. Quick recreate (TL;DR)

```bash
# on the UNO Q (over ssh):
arduino-app-cli app new la66 -d "LA66 LoRaWAN bridge"
#  paste sketch.ino (§4a) and main.py (§4b) into ~/ArduinoApps/la66/{sketch,python}/
arduino-app-cli app start user:la66
arduino-app-cli app logs  user:la66
#  set COMMANDS in main.py to e.g. ["AT","AT+DR=0","AT+SENDB=00,02,7,AC510100000000"]
#  stop/start to re-run
```

That gets you a LA66 transmitting correct LoRaWAN frames from the UNO Q's Linux
side. (Getting them received is the gateway/network-server problem, not covered.)
