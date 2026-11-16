# UNO Q + Dragino LA66 — Setup Runbook

How to drive a **Dragino LA66 LoRaWAN Shield** from the **Linux side** of an
**Arduino UNO Q**, and put real LoRaWAN frames on air.

**Scope / status:** this now covers the **whole chain**, from the UNO Q's Linux
side all the way to an MQTT client on the far side of a network server:

```
Linux Python → Bridge → STM32 → LA66 → RF → gateway → TTN → TTN MQTT → bridge.py → your MQTT broker → client
```

**What's verified (end to end):** an LA66 uplink was received by TTN (§8),
decoded, pulled off TTN's MQTT broker by a small bridge, and republished to a
downstream MQTT broker where a subscriber saw the value (§9). The original
"frames on air but not received" limitation is resolved — it was caused by the
**single-channel gateway** in the first setup; a proper 8-channel gateway fixed it.

- **§1–§4** — hardware, OS, shell, and the App (sketch + Python). *Device side.*
- **§5** — LA66 AT reference. *Includes un-pinning single-channel (`AT+CHS=0`).*
- **§8** — registering the device + gateway on **TTN** (ABP) and the uplink
  decoder. *§8e covers the single-channel-gateway path (what the first setup used).*
- **§9** — the **TTN → MQTT bridge**, and how to repoint it at any broker.
- **§10** — what changes to move the whole thing to **Loriot**.
- **§11** — pointing the LA66 at the **MultiTech MTCDT built-in NS** — the
  **ABP → OTAA** switch (and why it's needed), gateway registration, and the
  3-byte bird-detection payload/verification.

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
- **Single-channel vs full plan** (`AT+CHS`): the module may ship pinned to one
  frequency (`AT+CHS=868100000`). That's only needed for a *single-channel*
  gateway. With a **real 8-channel gateway** (what you want for TTN), **un-pin
  it**: `AT+CHS=0` → the LA66 uses the full EU868 channel plan and hops across
  channels (you'll see `TX on freq 868.100 / 867.100 / 867.500 …` on successive
  uplinks). Confirm with `AT+CHS=?` → returns `0`.
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
side. To get them *received* and out to MQTT, continue with §8 (TTN) and §9 (bridge).

---

## 8. Getting frames into TTN (network server)

This is the half that didn't work with the single-channel gateway. With a real
**8-channel EU868 gateway** it's straightforward. We used **ABP** with the
module's **factory session keys** (simplest — nothing to push down to the device).

### 8a. Register the gateway
- TTN Console → **Gateways → + Register gateway**. Enter the gateway EUI.
- **Frequency plan:** `Europe 863-870 MHz (SF9 for RX2 – recommended)`.
- Point the gateway's packet forwarder / Basics Station at the TTN EU1 server
  (`eu1.cloud.thethings.network`). Confirm the gateway shows **Connected** and
  is receiving traffic. (A **single-channel** packet forwarder is *not* supported
  by TTN — this is what broke the original setup.)

### 8b. Register the end device (ABP, factory keys)
1. Read the module's factory identity over the App (put `AT+CFG` in `COMMANDS`,
   run, read `app logs`). Ours reported:
   ```
   AT+DEUI=A8 40 41 4A 65 5D 11 3C     → DevEUI  A840414A655D113C
   AT+DADDR=1E6E6C89                    → DevAddr 1E6E6C89
   AT+NWKSKEY=12 53 22 …                → NwkSKey 12532281668F665FD5F9AA86CBFC2027
   AT+APPSKEY=D0 0A 46 …                → AppSKey D00A46A4E9A669D19CB3B8641FCE2C1F
   AT+NJM=0                             → ABP
   ```
2. TTN → **Applications → (your app) → + Register end device → Enter end device
   specifics manually**. LoRaWAN **1.0.3**, EU868, **Activation mode = ABP**.
3. Paste the **DevEUI, DevAddr, NwkSKey, AppSKey** exactly as read above. (Keys
   just have to match on both sides — reading the module's factory values *into*
   TTN is easier than pushing new ones to the module.)
4. **Enable frame-counter reset:** device → **General settings → Network layer →
   Advanced MAC settings → tick "Resets frame counters" → Save.** Without this,
   ABP uplinks whose counter isn't strictly increasing (e.g. after the module
   resets its `UpLinkCounter`) are **silently dropped**.

### 8c. Un-pin the channel and send
In `COMMANDS` (device side), before the first `SENDB`:
```
AT           # throwaway warm-up (boot-byte gotcha, §6)
AT+CHS=0     # full EU868 plan (see §5) — required for the multi-channel gateway
AT+SENDB=00,02,7,AC510C00000000   # unconfirmed, FPort 2, 7-byte payload
```
On **TTN → your device → Live data** you should see the uplink: **FPort 2**,
**FCnt** incrementing, payload `AC51xx000000`.

> **`rxDone` vs `rxTimeout`:** the *first* couple of uplinks often show `rxDone`
> (TTN sending initial ADR/MAC downlinks); later ones show `rxTimeout`. Downlinks
> are opportunistic — **`rxTimeout` is normal and does not mean the uplink was
> missed.** Proof of receipt is on TTN's Live data, not the radio RX window.

### 8d. Uplink payload decoder (TTN → Payload formatters → Uplink)
Matches the 7-byte `magic · tag · tick(u32 LE) · site` format the `common.py`
ingest expects. **Note the tag byte at offset 1** — a decoder that forgets it
reads `tick` one byte early (e.g. shows `849` instead of `3`).
```javascript
function decodeUplink(input) {
  var b = input.bytes;
  if (b.length < 7) return { errors: ["payload too short (need 7 bytes)"] };
  return {
    data: {
      magic: b[0],                                                   // 0xAC = 172
      device: String.fromCharCode(b[1]),                             // 0x51 = "Q"
      tick: (b[2] | (b[3] << 8) | (b[4] << 16) | (b[5] << 24)) >>> 0, // uint32 LE
      site: b[6]
    }
  };
}
```

### 8e. If you must use a single-channel gateway

A single-channel gateway (e.g. an ESP32/RPi + SX127x, or a Dragino
single-channel) **is what the original setup used**, and it's what caused the
"frames on air but never received" symptom. TTN does **not** officially support
single-channel packet forwarders — use one only for bench testing, and expect the
limitations below. If a proper 8-channel gateway is an option, prefer §8a–§8c.

To make a single-channel gateway work at all, the device and gateway must agree on
**one frequency *and* one spreading factor** (a single-channel radio listens to
exactly one of each):

- **Pin the device to the gateway's channel** — the opposite of §8c's un-pin:
  ```
  AT+CHS=868100000     # match the gateway's exact RX frequency (Hz)
  AT+DR=0              # SF12 — must match the gateway's fixed SF (DR0=SF12 … DR5=SF7)
  ```
  Confirm with `AT+CHS=?` (returns the frequency) and watch the log for
  `TX on freq 868.100 MHz at DR 0` — it must land on the gateway's channel/SF or
  the gateway simply won't hear it.
- **ABP only, unconfirmed only.** A single-channel gateway generally **can't send
  downlinks** reliably, so:
  - **OTAA won't join** (the join-accept is a downlink) → you must use **ABP**
    (`AT+NJM=0`), which is why the factory-ABP-keys route in §8b exists.
  - Send **unconfirmed** uplinks (`AT+SENDB=00,…`) — a confirmed uplink waits for
    an ACK downlink that never comes.
  - Expect **`rxTimeout` on every uplink** (no ADR, no MAC downlinks). That's
    normal here and, unlike §8c, permanent.
- **"Resets frame counters" still required** (§8b step 4).
- **Gateway registration:** register it on TTN as usual, but be aware TTN may flag
  it / it won't behave like a certified gateway. Fair-use and reliability are both
  worse than a real gateway.

Everything downstream (§8d decoder, §9 bridge, §10 Loriot) is **identical** — the
single-channel constraint only affects the device's channel/SF pinning and the
ABP-unconfirmed requirement, not how frames are consumed once TTN has them.

---

## 9. Fan out to an MQTT broker (the bridge)

**Key fact:** TTN has a built-in MQTT broker you **subscribe to** — it does *not*
push out to an arbitrary external broker. So to land data on your own broker you
run a small **bridge**: subscribe to TTN's MQTT, republish to your broker.

Files (in `bridges/`): `ttn_mqtt_bridge.py` *(TTN-era, not in this pack — main repo: `bridges/`)* +
`ttn_bridge.env` *(TTN-era, not in this pack — main repo: `bridges/`)* (credentials).

### 9a. TTN MQTT connection details
| | |
|---|---|
| Host | `eu1.cloud.thethings.network` (region-specific) |
| Port | `8883` (TLS) |
| Username | `<application-id>@ttn` |
| Password | a TTN **API key** with **"Read application traffic (uplink and downlink)"** |
| Subscribe topic | `v3/<application-id>@ttn/devices/+/up` |
| Payload | JSON; decoded fields at `uplink_message.decoded_payload`, raw bytes (base64) at `uplink_message.frm_payload` |

Create the API key: TTN → your Application → **API keys → + Add API key** → grant
*Read application traffic* → copy the `NNSXS…` value (shown once).

### 9b. Run it
1. Fill `ttn_bridge.env`: `TTN_APP_ID`, `TTN_API_KEY`, `TTN_REGION` (`eu1`).
2. `python ttn_hivemq_bridge.py`
3. It connects to TTN + the downstream broker, and prints each hop:
   ```
   uplink unoq3la66 fcnt=5 -> testtopic/unoqla66/tick = 12
   ```
The bridge reads `decoded_payload.tick`; if the TTN formatter (§8d) isn't
applied, it falls back to decoding `frm_payload` bytes itself, so it works either
way. It publishes the bare `tick` value to the topic — change the `hive.publish(...)`
line to send full JSON if you prefer.

### 9c. Pointing at a broker other than HiveMQ
It's **all in `ttn_bridge.env`** — no code change for a standard broker. The
`HIVEMQ_*` names are just legacy; they mean "the downstream broker":

| Var | Public HiveMQ (demo) | Your own broker (example) |
|---|---|---|
| `HIVEMQ_HOST` | `broker.hivemq.com` | `mqtt.example.org` |
| `HIVEMQ_PORT` | `1883` | `8883` (TLS) |
| `HIVEMQ_TOPIC` | `testtopic/unoqla66/tick` | `yeo/unoq/tick` |
| `HIVEMQ_USERNAME` | *(blank)* | `yeo-ingest` |
| `HIVEMQ_PASSWORD` | *(blank)* | `••••••` |
| `HIVEMQ_TLS` | *(blank)* | `1` |

The script applies `username_pw_set` when `HIVEMQ_USERNAME` is set and enables
TLS when `HIVEMQ_TLS=1` (use `HIVEMQ_PORT=8883` then). So moving from the public
demo broker to a private **Mosquitto / EMQX / HiveMQ Cloud** is a config edit
only. **⚠️ `broker.hivemq.com` is public** — anyone can read the topic; use it
only for demos, and point at an authenticated broker for anything real.

---

## 10. Repointing to Loriot (instead of TTN)

Moving the pipeline to **Loriot** touches three places; the **device side is
untouched** — the LA66 keeps sending the same ABP frames on EU868, oblivious to
which network server is behind the gateway.

**1. Gateway.** Repoint the gateway's upstream from TTN to Loriot: register the
gateway in the Loriot console and configure its packet forwarder / Basics Station
to Loriot's server for your region. A gateway forwards to **one** network server
at a time — so this is a swap, not an addition (running both needs a UDP packet
multiplexer or a second gateway).

**2. Device registration.** Create the application + device in Loriot. Easiest is
to reuse **ABP** with the **same DevAddr / NwkSKey / AppSKey** (import them the
same way you did on TTN), so nothing changes on the module. Loriot has its own
equivalent of the ABP frame-counter-reset allowance — enable it for a test device.

**3. The bridge (upstream half).** This is the real code change. Loriot does **not**
expose the same MQTT topic scheme as TTN; its **Application Output** options are
its own (WebSocket streaming, HTTP push, and MQTT on some tiers). So in
`ttn_hivemq_bridge.py` you replace the **TTN-subscribe half** with a Loriot
consumer:
- Point at Loriot's output endpoint (WebSocket URL + token, or Loriot's MQTT
  broker if your tier has it) instead of `eu1.cloud.thethings.network`.
- Parse **Loriot's** uplink JSON, not TTN's. Loriot puts the raw payload as **hex**
  in a `data` field (TTN uses base64 in `frm_payload` and a `decoded_payload`
  object). So the tick extraction becomes:
  ```python
  b = bytes.fromhex(msg["data"])          # Loriot: hex string
  tick = int.from_bytes(b[2:6], "little") # same magic·tag·tick·site layout
  ```
- The **downstream half is unchanged** — you still republish `tick` to your
  broker via the same `HIVEMQ_*` env settings (§9c).

In short: **device = no change, gateway = repoint upstream, TTN device reg →
Loriot device reg, bridge = swap the source half (TTN MQTT/JSON → Loriot
output/JSON), keep the downstream publish.**

---

## 11. Pointing the LA66 at the MultiTech built-in Network Server (ABP → OTAA)

This is what we actually did to route `yeo-unoq-3`'s LA66 to the **CeLab MTCDT's
own built-in Network Server** (the same NS the MKR WAN 1310 test node joined),
instead of TTN/Loriot. The gateway is `mtcdt` @ `10.129.122.70`, running in
**Network Server** mode (full setup in `../3-gateway/README.md`).

### 11.0 The gotcha that cost us the most time — ABP vs OTAA

- The LA66 was left in **ABP** (`AT+NJM=0`) with the TTN factory *session* keys
  (DevAddr `1E6E6C89`, NwkSKey `1253…`, AppSKey `D00A46…`).
- The MTCDT registers end devices through **Key Management → Local Join Server →
  Local End-Device Credentials**, which is an **OTAA join server** — there is
  **no ABP/OTAA toggle**, you "just fill in all the details" and it is implicitly
  OTAA. ABP uplinks from the module therefore **never match** any registration.
- **Symptom of the mismatch:** on the gateway the device shows **Last Seen =
  unknown** and there is **no row in the Sessions table**. The LA66's own log
  looks "fine" (it transmits, `txDone`, `UpLinkCounter` increments) because ABP
  needs no join — but the NS silently drops every frame.
- **Fix = put the module in OTAA** so it matches the join-server registration.
  (Alternatively you could register a manual **ABP session** under *Sessions →
  Add New* with the DevAddr + session keys, but the join-server/OTAA route is the
  one the gateway is set up for and is cleaner — no frame-counter-reset caveat.)

### 11a. Register the device on the MTCDT (two places — mirrors the MKR)

1. **LoRaWAN → Devices → Add New:** Device EUI = the LA66's DevEUI
   (`a8-40-41-4a-65-5d-11-3c`), a name (`yeo-unoq-3`), Class A.
2. **LoRaWAN → Key Management → Local Join Server → Local End-Device Credentials
   → Add New:** Device EUI, **AppEUI `00-00-00-00-00-00-00-00`**, **AppKey = the
   LA66's *factory* AppKey** (read it from `AT+CFG` → the `AT+APPKEY=…` line;
   ours ended `…2795404F`), Device Profile `LW102-OTA-EU868`, Network Profile
   `DEFAULT-CLASS-A`.
   - **You do not need to change the module's AppKey.** Register whatever the
     module already has. Each device uses its **own** per-device key here.
   - The **Local Network Settings** block on that page holds a *network-wide
     default* AppKey (ours `540E97EB4326883EF464C86D9528F566`, which the MKR
     uses). A per-device credential **overrides** the default, so the LA66 and
     the MKR each join with their own factory key.
3. **Save and Apply.**

### 11b. Switch the LA66 from ABP to OTAA and join

On the device side, issue these to the LA66 (via the RPC `main.py`, or raw
`lora.py` — whatever is driving it). The **AppEUI must match** the join-server
entry, and the AppKey is left at the factory value already registered above:

```
AT+NJM=1                              # OTAA  (was 0 = ABP)
AT+APPEUI=00 00 00 00 00 00 00 00     # match the gateway Join Server AppEUI
                                      # (AppKey UNCHANGED — factory ...2795404F)
AT+ADR=0
AT+DR=0                               # DR0 = SF12 (max range)
AT+CHS=0                              # full 8-channel EU868 plan (NOT single-channel)
ATZ                                   # reset: applies NJM/CHS *and* auto-sends the OTAA join
```

After `ATZ` the module reboots and **auto-sends a join request**. Confirm the
join with `AT+NJS=?` → **`1`** (joined). If it hasn't joined within ~20 s, send
`AT+JOIN` to retry. OTAA works here because the MTCDT is a real **8-channel**
gateway and can return the join-accept downlink (a single-channel gateway
cannot — that was the whole §8e limitation).

> **Revert to ABP** later is just `AT+NJM=0` — the ABP session keys are still
> stored in the module.

### 11c. The payload — 3-byte bird detection (not the 7-byte tick beacon)

For the bird node we send the **same 3-byte payload the MKR WAN 1310 sketch
used**, on **FPort 2**, unconfirmed:

```
byte 0-1 : species ID        (uint16, big-endian)   e.g. 2441
byte 2   : confidence x 100   (uint8, 70..91)         -> /100 = 0.70..0.91
```

AT form: `AT+SENDB=00,02,3,<hex>`, e.g. `AT+SENDB=00,02,3,098955` = species
`0x0989`=2441, confidence `0x55`=85 → 0.85.

Two senders exist, same payload, different transport:
- **`bird_sender.py`** — the raw-socket path (`lora.py` → `:7500` →
  pass-through sketch). Byte-for-byte port of the MKR sketch. Preferred for the
  standalone inference pipeline. See [`LA66_RPC_VS_RAW.md`](LA66_RPC_VS_RAW.md).
- **`~/ArduinoApps/la66/python/main.py`** (bird variant) — the **RPC** path
  (`la66_send`/`la66_drain` over the Bridge). **This is what we actually ran**,
  because the board was found running the RPC Bridge sketch (not the raw
  pass-through), and the RPC path avoids the `:7500` contention. It also does the
  §11b `NJM=1`/`AppEUI`/`CHS=0`/`ATZ` config on first loop, waits for `NJS=1`,
  then sends a detection every 180 s. The original Loriot tick-beacon `main.py`
  is backed up alongside as `main.py.tickbeacon.bak`.

### 11d. Verify (device side, then NS side)

**Device log** — a good uplink after joining:
```
JOINED
TX #1 OK species=1783 conf=0.70 cmd=AT+SENDB=00,02,3,06F746
***** UpLinkCounter= 0 *****
TX on freq 867.500 MHz at DR 0     (hops channels each uplink = CHS=0 working)
txDone
RX on freq 869.525 MHz at DR 2 → rxDone   Rssi=-27   (NS is answering!)
```
`rxDone` (instead of `rxTimeout`) on the RX window means the NS accepted the
uplink and sent a downlink back — the clearest device-side proof it's landing.

**Gateway** — refresh **LoRaWAN → Devices / Sessions**:
- **Devices:** `Last Seen` flips from *unknown* to a recent time.
- **Sessions:** a **new row** for the DevEUI appears with an **NS-assigned
  Dev Addr** (ours came out `00895a7d`) and a climbing **Up FCnt**.

**A confirmed uplink** as the NS emits it (Packets view / MQTT). Note `data` is
the **raw payload base64** — the NS does **no field decoding**:
```json
{"deveui":"a8-40-41-4a-65-5d-11-3c","devaddr":"00895a7d",
 "appeui":"00-00-00-00-00-00-00-00","port":2,"size":3,"data":"BvdG",
 "datr":"SF12BW125","freq":867.5,"fcnt":0,"gweui":"00-80-00-00-a0-00-81-47"}
```
`data:"BvdG"` → `06 F7 46` → species `0x06F7`=**1783**, confidence `0x46`=**70**
→ 0.70. (`"BvdL"` → `06 F7 4B` → species 1783, conf 75.) Both MTCDT mCards
(`…81:46` and `…81:47`) hear it, and the frequency changes per uplink — both
confirm the full-channel-plan OTAA setup is healthy.

### 11e. Downstream (cetools) — decode the 3 bytes, don't read a counter

The NS publishes the payload as raw base64 in `data` (topic
`lora/a8-40-41-4a-65-5d-11-3c/up`, bridged to cetools exactly like the MKR — see
`../3-gateway/README.md`). Wherever you consume it, split the 3 bytes:

```python
b = base64.b64decode(msg["data"])        # 3 bytes
species    = int.from_bytes(b[0:2], "big")
confidence = b[2] / 100.0
```

This replaces the MKR's 4-byte big-endian counter decode, and the 7-byte
`magic·tag·tick·site` beacon decoder in §8d. The equivalent TTN-style JS decoder
is at the bottom of `bird_sender.py`.
