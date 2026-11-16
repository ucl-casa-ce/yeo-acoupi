# LA66 on UNO Q — session handoff / state-of-play

**One-line status:** A standalone UNO Q can send LoRaWAN uplinks to TTN through
the Dragino LA66 via a **raw-socket path** (`lora.py` → `:7500` → pass-through
sketch on the STM32). It **works while the Arduino App is `start`ed**, but that
couples it to the ~270 MB framework *and* causes an intermittent `:7500`
contention. The **open question** (mid-test) is whether the STM32 runs the
pass-through sketch on a **cold power-on without the App**, which would unlock a
lean, autonomous, framework-free image. Read this top-to-bottom before touching
the board.

Companion docs (already in this repo):
- [`UNO_Q_LA66_SETUP.md`](UNO_Q_LA66_SETUP.md) — full runbook (RPC path, TTN reg, MQTT bridge, Loriot, single-channel).
- [`LA66_RPC_VS_RAW.md`](LA66_RPC_VS_RAW.md) — the architecture decision record.

---

## 1. The end-state we're building toward

A field device that, **on power-up alone** (USB unplugged, WiFi off):
1. comes up with the Arduino framework in a **RAM-frugal** state,
2. runs the on-board comms path that forwards commands to the LA66,
3. **ticks a LoRaWAN uplink every ~3 min**, provable with the board fully
   untethered (verify on TTN Live data from a browser),
4. then repeat the **TTN → MQTT** relay test (to `mqtt.cetools.org`).

Eventually the tick payload is replaced by real bat/bird detection data from the
inference pipeline (see the handover overview in `../README.md`).

## 2. Hardware & access

- **Board:** Arduino UNO Q — **two** processors: a Qualcomm **Linux** side (Debian
  13, where you SSH/adb) and an **STM32U585** MCU. *Every Arduino header pin,
  incl. D0/D1, belongs to the STM32*, so Linux can't open the LA66 UART directly.
- **Shield:** Dragino LA66 (EU868), stacked. `TXD→D0 (RX)`, `RXD→D1 (TX)`, on
  `Serial1` @ 9600 8N1. Antenna attached. `JP6` fitted.
- **Hostname:** `yeo-unoq-3`, user **`arduino`**. adb serial `2354301559`.
- **Access:** `adb shell` over USB (reliable) **or** `ssh arduino@yeo-unoq-3.local`.
  ⚠️ On the current WiFi (`10.134.231.x`, managed net) **mDNS/NetBIOS do NOT
  resolve** — `.local` fails; get the IP from the router's DHCP list, or just use
  `adb` over USB.
- **Power:** runs on this PC's USB (adb works) *and* on a 5 V/3 A brick. Runbook
  warns of brown-out on weak USB ports with the shield stacked — use the brick if
  boot is flaky. **Unplugging/replugging USB is a cold power-cycle for the STM32.**

## 3. TTN configuration (already done on TTN)

- **Application:** `water-quality-mfoster`, region **eu1**.
- **Device:** ABP, LoRaWAN 1.0.3, EU868, using the LA66's **factory session keys**:
  - DevEUI `A840414A655D113C`
  - DevAddr `1E6E6C89`
  - NwkSKey `12532281668F665FD5F9AA86CBFC2027`
  - AppSKey `D00A46A4E9A669D19CB3B8641FCE2C1F`
  - **"Resets frame counters" = ON** (required for this ABP test device).
- **Gateway:** a real 8-channel EU868 gateway registered to TTN (single-channel
  is *not* supported — that was the original "not received" failure).
- **Uplink decoder** (Payload formatters → Uplink) — see `UNO_Q_LA66_SETUP.md` §8d.
- **API key** for the MQTT bridge lives in `ttn_bridge.env` (`NNSXS…`).

**Payload = 7 bytes:** `magic(0xAC) · tag(0x51='Q') · tick(uint32 LE) · site(1)`.
e.g. `tick=13` → `AC510D00000000`. Decodes to `{magic:172, device:"Q", tick, site}`.

## 4. Files

### In this repo (`C:\Users\markf\CodeLocal\ucl\yeo\`)
| File | What |
|---|---|
| `lora.py` | **Robust raw-socket LA66 driver** — one lock-guarded connection, background reader, waits for `txDone`, EU868 duty-cycle pacing, `DiskSpoolSender` (crash-durable queue), CLI (`python3 lora.py <tick>`). |
| `tick_sender.py` | 3-min periodic tick sender using `lora.py`; persists tick in `~/.yeo_tick`; forces DR0/SF12 + full plan. |
| `ttn_mqtt_bridge.py` | TTN-MQTT → downstream-MQTT bridge. **Runs on a PC/server, NOT the device.** Reads `ttn_bridge.env`. |
| `ttn_bridge.env` | Creds: `TTN_APP_ID=water-quality-mfoster`, `TTN_API_KEY=NNSXS…`, `TTN_REGION=eu1`, `MQTT_HOST=mqtt.cetools.org`, `MQTT_PORT=1884`, `MQTT_TOPIC=testtopic/unoqla66/tick`, `MQTT_USERNAME=student`, `MQTT_PASSWORD=…`. |
| `UNO_Q_LA66_SETUP.md`, `LA66_RPC_VS_RAW.md` | runbook + decision record. |

### On the board (`~/ArduinoApps/la66/`)
| Path | State |
|---|---|
| `sketch/sketch.ino` | The **pass-through** sketch (`Serial`↔`Serial1` relay). Last confirmed version still has `while(!Serial);`. A drafted improvement **removes** that line (hang risk when framework-free) — **not confirmed flashed yet**. |
| `python/main.py` | **Inert** (`import time; while True: time.sleep(3600)`) — keeps the App "started" without touching `:7500`. |
| `~/sketch.ino.rpc.bak` | backup of the original RPC sketch (to revert). |
| `~/lora.py`, `~/tick_sender.py` | copies the user placed on the board (stdlib-only, no pip). |

**Flashing:** `arduino-app-cli app start user:la66` compiles `sketch.ino` and flashes
the STM32 via **OpenOCD SWD**, then starts the Python container. `app stop` **halts
the MCU**.

## 5. Hard-won facts & gotchas (the important part)

1. **`:7500` is a transparent byte tunnel** to the STM32's `Serial`, provided by
   **`arduino-router`** (pid ~600, ~10 MB) — **not** `arduino-app-cli`. The
   *flashed sketch* decides whether those bytes are **RPC frames** (Bridge sketch)
   or **raw AT** (pass-through sketch).
2. **`arduino-app-cli app stop` HALTS the STM32** (the "Stopping microcontroller…"
   line). So the pass-through sketch only runs while the App is `start`ed — *or*,
   hypothetically, after a cold power-on (untested, see §6).
3. **The App framework contends on `:7500`.** When the App is `start`ed, the
   framework keeps its **own** connection to the router's tunnel (independent of the
   inert `main.py`), stealing bytes from `lora.py`:
   - big reads (`AT+CFG`) come back **garbled**;
   - sometimes `lora.py` gets **nothing** (`got []`);
   - short sends (`AT+SENDB`→`txDone`) *sometimes* slip through — that's how
     **FCnt 8 landed** and how `tick_sender.py` ran — but it's a **race, not
     reliable.** This is the core reason the raw path and the running framework
     don't coexist well.
4. **A warm `sudo reboot` does NOT bring the MCU up** (with the App stopped):
   `:7500` is up but `lora.py` gets `[]`. `sudo gpioset -c /dev/gpiochip1 -t0 70=1`
   (mirroring the router's `--after-ready` release) **alone did not** wake it.
5. **`app start` brings the MCU up via OpenOCD SWD flash-and-run** (halt Cortex-M33
   → write flash → detach → run). That reset/run is the one thing genuinely tied to
   the framework.
6. **RAM is not actually scarce:** ~**2.9 GB free** even with framework + desktop.
   Framework ≈ 270 MB (`arduino-app-cli` 126 + `dockerd` 85 + `containerd` 42 +
   shim 18 + app python 44). Desktop ≈ 200 MB (`Xorg` + `lightdm`). Router ≈ 10 MB.
   So framework-free is an **optimization**, not a blocker.
7. **Duty cycle:** SF12 airtime ≈ 1.3 s; EU868 1% ⇒ ~131 s off-air ⇒ a 3-min
   interval is safe. `lora.py` paces internally as a backstop.

## 6. THE OPEN QUESTION / next experiment (we stopped here)

**Does the STM32 run the pass-through sketch on a *cold power-on* without the App?**
Warm reboot didn't; a real power-cycle gives the MCU a power-on-reset that *should*
run its flashed program. This decides the whole architecture.

**Blocker hit:** SSH-over-WiFi name resolution fails on this network, and the board
is currently off USB. **Do the test over USB/adb instead** (USB replug = cold boot):

```bash
# 0. (recommended) flash the improved pass-through sketch first — drop while(!Serial):
#    put this in ~/ArduinoApps/la66/sketch/sketch.ino:
#      void setup(){ pinMode(1,OUTPUT); digitalWrite(1,HIGH);
#        pinMode(0,INPUT_PULLUP); delay(100); pinMode(0,INPUT);
#        Serial.begin(9600); Serial1.begin(9600); }
#      void loop(){ if(Serial.available()) Serial1.write(Serial.read());
#                   if(Serial1.available()) Serial.write(Serial1.read()); }
arduino-app-cli app start user:la66      # flash it
arduino-app-cli app stop  user:la66      # so the App won't run/contend after boot

# 1. cold power-cycle: unplug USB, wait ~10s, replug (or power from the brick)
# 2. WITHOUT app start, from adb/ssh:
ss -tlnp | grep 7500
python3 ~/lora.py 8
```

- **`txDone`** → MCU self-runs on power → **go framework-free**: keep only
  `arduino-router` for `:7500`, disable `arduino-app-cli` + Docker + desktop
  (`systemctl disable --now …`), install `tick_sender.py` as a systemd service
  (`yeo-lora-ticker`), reclaim ~470 MB. Then the untethered demo + MQTT test.
- **`got []`** → MCU needs the OpenOCD reset-run → framework-free requires a
  boot-time "reset-and-run" oneshot (extract the OpenOCD invocation from
  `arduino-app-cli`), **or** accept keeping the App running (unreliable due to the
  §5.3 contention, which would push back toward the RPC model).

## 7. Reproduce the current known-good (raw path, App running)

```bash
# sketch.ino = pass-through (§4/§6),  main.py = inert
arduino-app-cli app start user:la66      # flash + run (MCU up, container inert)
python3 ~/lora.py 1                       # AT+CFG may garble, but expect a txDone
python3 ~/tick_sender.py                  # ticks every 3 min (sends survive the contention)
```
Verify on **TTN Live data** (FPort 2, `AC51<tick>000000`, FCnt increments) and, for
the MQTT leg, run `python3 ttn_mqtt_bridge.py` on a PC/server (reads `ttn_bridge.env`).

## 8. Revert to the RPC path (if needed)

```bash
cp ~/sketch.ino.rpc.bak ~/ArduinoApps/la66/sketch/sketch.ino
#  restore an RPC main.py (see UNO_Q_LA66_SETUP.md §4b), then:
arduino-app-cli app start user:la66
```

## 9. What's proven vs not

- ✅ **RPC path**: full chain LA66 → TTN → MQTT (`ttn_mqtt_bridge.py`), verified.
- ✅ **Raw path**: `lora.py` put **FCnt 8** on TTN; `tick_sender.py` ran — *but*
  with the App running and its `:7500` contention (works, not yet robust).
- ❓ **Framework-free / cold-boot self-run**: the pending §6 test.
- ❌ **Untethered (brick + WiFi off) demo** and the **RAM-slim image**: not yet done
  (depend on §6).
