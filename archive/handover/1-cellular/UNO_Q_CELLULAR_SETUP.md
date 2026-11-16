# UNO Q + Waveshare A7670E — Cellular Setup Runbook

The cellular counterpart of [`UNO_Q_LA66_SETUP.md`](../2-la66-lora/UNO_Q_LA66_SETUP.md). It takes
a UNO Q from bare board to **publishing telemetry over 4G to `mqtt.cetools.org`**,
using the Waveshare A7670E Cat-1 modem. Every step we actually used to get the
`yeo-unoq-4` node live is here, with the gotchas that cost time called out.

> **Current SIM (2026-07-18): KeySIM (Tele2), APN `key`, roaming on
> Vodafone-UK.** This runbook was written against the earlier Giffgaff SIM
> (APN `giffgaff.com`, O2), so wherever you see `giffgaff.com` below, substitute
> `key`. Everything else in the sequence is unchanged.
>
> The APN has to match the SIM or you get `AT+CEREG? → 0,3` — data registration
> *denied* — with full signal and a network found, which reads like a coverage
> problem and isn't. Set it and force a fresh attach:
> `AT+CGDCONT=1,"IP","key"` then `AT+CFUN=1,1`. IoT/roaming SIMs can also take
> 15–60+ minutes to provision after activation, sitting at `0% / idle` until
> they do. See `4-unoq/a-board-setup/UNO-Q-Quirks-and-Gotchas.md` for the full
> SIM/registration checklist.

Companion docs already in the repo:
- `waveshare_cellular/CellularSetup.md` *(not in this pack — main repo: `yeo-acoupi/waveshare_cellular/`)*
  — the original bring-up notes: power isolation, minicom, `+CREG:3`/LTE, SMS,
  and **why PPP is a dead end on this board**. Deeper AT/power background lives
  there; this file is the end-to-end runbook.
- [`cell_modem.py`](code/cell_modem.py) — the reusable cellular driver
  (the sibling of `lora.py`).
- [`cell_bird_sender.py`](code/cell_bird_sender.py) — the sender app that uses it.

---

## 0. The two things you must understand first

1. **We drive the modem's *own* MQTT stack over AT commands, not PPP.** This
   board's stripped Debian kernel has **no `ppp_generic` module**, so `pon`/`ppp0`
   can never come up (`modprobe: FATAL: Module ppp_generic not found`). You cannot
   use `paho`/`requests` over a cellular network interface. Instead the modem
   firmware has a built-in MQTT client you drive with `AT+CMQTT*` over its serial
   port. That is the whole architecture. (Full diagnosis in `CellularSetup.md`.)

2. **ModemManager will fight you — mask it.** Debian runs **ModemManager**, which
   grabs the modem's `ttyUSB` nodes and issues its own AT/connection commands.
   Against our raw-AT usage that produces a parade of *misleading* symptoms —
   `+CME ERROR: SIM failure`, `+CSQ: 99,99`, `+CEREG: 3` denied, `CGACT` "unknown
   error", and pyserial `device reports readiness to read but returned no data
   (multiple access on port)`. The SIM/signal are fine; ModemManager is the cause.
   **`sudo systemctl mask --now ModemManager`** and it all clears instantly. This
   is step one of §3 for a reason — do it before anything else.

Unlike the LA66 path, the cellular path uses the UNO Q's **Linux side only** — the
STM32 and the Arduino "App" framework are not involved at all.

---

## 1. Hardware

- **Board:** Arduino UNO Q (Qualcomm Dragonwing Linux side, Debian 13). The modem
  is a plain USB device to Linux; no sketch, no `:7500`, no STM32.
- **Modem:** Waveshare **A7670E-LASE** = SIMCom A7670E, Cat-1 LTE, EU bands.
  USB `1e0e:9011` ("SIMCom Wireless Solution / A76XX Series"). It enumerates as
  **three** `/dev/ttyUSB{0,1,2}` nodes (USB interfaces 02/04/05, `option` driver).
  **More than one answers `AT`, and the AT-port number is NOT stable** across
  reboots/resets — so we auto-detect it, never hard-code it (see §4/§10).
- **USB hub:** everything hangs off a Super Top 4-port hub (`14cd:8601`). On the
  same hub in our build:
  - **AudioMoth USB mic** (`16d0:06f3`) → ALSA sound card (`/proc/asound/cards`),
    **not** a `ttyUSB`.
  - **microSD reader** (`14cd:1212`) → block device `/dev/sda`, **not** a `ttyUSB`.
  So if you see multiple USB devices, only the SIMCom one is the modem; the mic
  and SD card never appear as serial ports.
- **Power — the classic brownout trap.** The Cat-1 radio pulls **up to ~2 A**
  transient spikes when it wakes/transmits. A host USB port (0.5–0.9 A) cannot
  sustain that; the voltage sag **silently crashes/reboots the UNO Q's Linux**
  (symptom during early bring-up: `No route to host`, lockups). **Give the modem
  its own ≥2 A supply**; run the board on mains. Never bridge 5 V between board
  and HAT. (Details in `CellularSetup.md §1`.)
- **LEDs (sanity glance):** one **solid** red = powered; a second red **flickering**
  = network/data activity. Solid-only with no flicker = not attached.
- **SIM:** *(current: KeySIM / Tele2, **APN `key`**, roams onto Vodafone-UK.)*
  As originally written: Giffgaff (O2 MVNO), 30-day data bundle. **APN `giffgaff.com`**, no
  user/pass. Our SIM actually **registers roaming onto EE** on LTE (`+CEREG: 0,5`,
  `+COPS: …,"EE",7`) — roaming is normal and fine. **Force LTE-only** (`AT+CNMP=38`):
  UK 3G is being retired, and an "auto" scan attaching to 3G gets `+CREG: 3`
  (registration denied).

---

## 2. Get a shell on the UNO Q

Two ways in (live specifics below):

- **Over the network (mains / no USB):** `ssh arduino@<board-ip>`. Our node
  `yeo-unoq-4` (hostname `dave`) sits on the `nextguest` Wi-Fi at
  **`192.168.68.116`**. Login/sudo password: **`<password>`**. Find the IP from the
  router's DHCP table if it moves. (adb-over-TCP/5555 is **not** enabled; SSH/22 is.)
- **Over USB:** `adb shell` (the board exposes an ADB interface, USB `2341:0078`
  MI_00). Reliable but only while USB is plugged.

`systemctl --user …` over SSH needs the runtime dir set:
```sh
export XDG_RUNTIME_DIR=/run/user/$(id -u)   # arduino is uid 1000 → /run/user/1000
```

> **Windows tip.** Driving this from Windows, `ssh`/`scp` fight you over quoting
> and MSYS path mangling. Put remote command sequences in a `.sh`, `pscp` it over,
> and run it — don't inline multi-command strings with `(`, `$( )`, or `;`. PuTTY
> `plink -pw <password>` / `pscp -pw <password>` work well; native OpenSSH with a key
> installed (below) is cleanest.

Optional but recommended — install your key for passwordless access:
```sh
mkdir -p ~/.ssh && chmod 700 ~/.ssh
echo 'ssh-ed25519 AAAA…your-key… comment' >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
```
(If SSH later complains the host key "changed", it's just a recycled DHCP lease:
`ssh-keygen -R 192.168.68.116`.)

---

## 3. Prerequisites on the board (do these first)

### 3a. Mask ModemManager — THE critical step
```sh
sudo systemctl disable --now ModemManager
sudo systemctl mask ModemManager
# verify:
systemctl is-active ModemManager     # -> inactive
systemctl is-enabled ModemManager    # -> masked
```
`mask` (not just `disable`) so nothing can pull it back in, and it stays off across
the power-cycle reboots the field device will do. **If cellular ever breaks later,
check this first.**

### 3b. Install pyserial (no sudo needed)
Debian marks the system Python "externally managed" (PEP 668), so:
```sh
python3 -m pip install --user --break-system-packages pyserial   # -> pyserial 3.5
python3 -c "import serial; print(serial.__version__)"
```
Installs to `~/.local`; the systemd **user** service (§7, runs as `arduino`) picks
it up automatically.

### 3c. Confirm the modem enumerated
```sh
lsusb | grep -i simcom            # -> 1e0e:9011 … A76XX Series LTE Module
ls -l /dev/ttyUSB*                # -> ttyUSB0/1/2, group dialout
groups | grep -q dialout && echo "can open serial"   # arduino IS in dialout
```

---

## 4. The software: a driver + an app (mirrors `lora.py` + `bird_sender.py`)

Two files, both in `~/` on the board. Keep them **in the same directory** — the app
imports the driver.

- **`cell_modem.py` — the cellular interface layer (the driver).** Class
  `CellModem` wraps the A7670E's AT/CMQTT stack behind a clean API so nothing else
  touches raw serial:
  - **`autodetect`** — scans `/dev/ttyUSB*`/`ttyACM*` for the node that answers
    `AT`→`OK` (handles the unstable port number).
  - **exclusive open** (`TIOCEXCL`) so a second opener / a resurrected ModemManager
    can't silently share the port.
  - **`at()`** with retries + a modem-ready wait; **`at_prompt()`** for the
    `>`-prompt `CMQTTTOPIC/PAYLOAD` pattern.
  - **`bring_up()`** — SIM-ready (with retry) → LTE lock → `+CEREG` registration →
    signal gate → PDP context, **verified by a real IP from `CGPADDR`** (not by
    `CGACT`'s say-so — see §10).
  - **`mqtt_connect/publish/disconnect`** with session-state tracking (clean,
    idempotent teardown) and real CMQTT result-code parsing.
  - **`status()`** health snapshot, **`radio_off()`**, **`reset()`**.
  - Typed errors: `ModemNotPresent` / `ModemBusy` / `SimError` /
    `RegistrationError` / `MqttError`, and a **ModemManager hint** appended to
    busy/failure messages.
  - **CLI:** `python3 cell_modem.py` → opens, brings up, prints a status JSON.
    Your fastest field diagnostic.

- **`cell_bird_sender.py` — the app.** Generates simulated bird detections
  (same species IDs / 0.70–0.91 confidence as `bird_sender.py`, so the `hex` matches
  the LoRa payload) and ships them via the driver. Offline-first:
  - writes each detection atomically to a **disk spool** (`~/.yeo_bird_spool/*.json`)
    **before** any modem work — generation never blocks on the radio;
  - **batches**: flush when `BATCH_SIZE` queued or `FLUSH_MAX_AGE` elapsed;
  - a spool file is deleted **only after a confirmed `+CMQTTPUB`** — nothing lost,
    nothing double-sent; failures retry up to `MAX_ATTEMPTS` then move to `*.failed`;
  - `SPOOL_CAP` bounds disk if the modem is away a long time (drops oldest);
  - **graceful when the modem is absent/busy** — logs and keeps spooling.
  - Flags: `--once` (spool one + flush + exit), `--flush-only` (drain, don't generate).

Deploy them:
```sh
scp cell_modem.py cell_bird_sender.py arduino@<ip>:/home/arduino/
```

---

## 5. Configuration — `~/.yeo_cell.env`

All config is environment-driven. Create `~/.yeo_cell.env` (chmod 600 — it holds
the broker password):

```sh
# --- modem ---
YEO_MODEM_PORT=auto            # scan for the AT port; DON'T hard-code ttyUSBx
YEO_MODEM_BAUD=115200
YEO_APN=key                    # KeySIM/Tele2 (current). Giffgaff was giffgaff.com
YEO_LTE_ONLY=1                 # AT+CNMP=38 (dodge the dead 3G / +CREG:3)
# --- MQTT broker (CeTools) ---
YEO_MQTT_HOST=mqtt.cetools.org
YEO_MQTT_PORT=1884             # plain TCP, no TLS
YEO_MQTT_USER=student
YEO_MQTT_PASS=<password>
YEO_MQTT_TOPIC=student/yeo/lora/yeo-unoq-4/up
YEO_MQTT_CLIENTID=yeo-unoq-4
YEO_MQTT_QOS=1
YEO_DEVICE_ID=yeo-unoq-4
YEO_SITE=0
# --- cadence / batching ---
YEO_GEN_INTERVAL=180           # seconds between generated detections
YEO_BATCH_SIZE=5               # flush once this many are queued
YEO_FLUSH_MAX_AGE=1800         # ...or this many seconds since last flush
YEO_RADIO_OFF=1                # CFUN=0 between bursts (power saving) — see §8
YEO_SPOOL_CAP=1000
YEO_SPOOL_DIR=/home/arduino/.yeo_bird_spool
```
```sh
chmod 600 ~/.yeo_cell.env
```

> **Topic + ACL gotcha (important).** The shared cetools **`student` account is
> ACL-locked to `student/#`**. Publish anywhere else (e.g. `yeo/…`) and the broker
> **silently drops it** (QoS 0 gives no error; even QoS 1 `PUBACK`s per MQTT 3.1.1).
> The topic **must** live under `student/…`. We use
> `student/yeo/lora/<device-name>/up`, mirroring the LoRaWAN→cetools topic
> (`student/yeo/lora/<DevEUI>/up`) with the device-EUI segment replaced by the
> friendly name `yeo-unoq-4`. `CEDevice` is the *other* account, the one allowed to
> publish `yeo/…` (that's what acoupi uses). Verify a publish actually lands with a
> subscriber (§9) — don't trust `+CMQTTPUB: 0,0` alone.

Optional launcher `~/run_cell_bird.sh` (sources the env, then runs — handy for
manual runs and the service):
```sh
#!/bin/sh
set -a; . /home/arduino/.yeo_cell.env; set +a
exec /usr/bin/python3 /home/arduino/cell_bird_sender.py "$@"
```
```sh
chmod +x ~/run_cell_bird.sh
# smoke test (drains one detection now):
sh ~/run_cell_bird.sh --once
```

---

## 6. What the driver does on the wire (AT / CMQTT reference)

You never type these by hand — `cell_modem.py` runs them — but here's the exact
sequence, in order, for debugging with `minicom -D /dev/ttyUSBx -b 115200 -o`
(the `-o` skips flow control; without it the terminal ignores your keystrokes):

**Bring-up (network):**
```
AT                       # responsive?
ATE0                     # echo off (clean parsing)
AT+CMEE=2                # verbose +CME errors
AT+CFUN=1                # radio on
AT+CPIN?                 # -> +CPIN: READY   (retry a few times if not)
AT+CNMP=38               # LTE only
AT+CEREG?                # wait for +CEREG: x,1 (home) or x,5 (roaming). x,3 = DENIED
AT+CSQ                   # signal (99,99 = none)
AT+CGDCONT=1,"IP","key"  # KeySIM/Tele2 (current); Giffgaff was "giffgaff.com"
AT+CGACT=1,1             # activate PDP — MAY error if network pre-activated it (OK)
AT+CGPADDR=1             # -> +CGPADDR: 1,<ip>  ← the REAL proof we have data
```

**Publish (CMQTT stack), per window:**
```
AT+CMQTTSTART                                   # -> +CMQTTSTART: 0
AT+CMQTTACCQ=0,"yeo-unoq-4",0                   # ,0 = no TLS
AT+CMQTTCONNECT=0,"tcp://mqtt.cetools.org:1884",60,1,"student","<pass>"
                                                # async -> +CMQTTCONNECT: 0,0 (0=ok)
# for each message:
AT+CMQTTTOPIC=0,<len>   > <topic bytes>
AT+CMQTTPAYLOAD=0,<len> > <payload bytes>
AT+CMQTTPUB=0,1,60                              # -> +CMQTTPUB: 0,0 (0=ok)
# teardown:
AT+CMQTTDISC=0,60
AT+CMQTTREL=0
AT+CMQTTSTOP
AT+CFUN=0                                        # only if YEO_RADIO_OFF=1
```

---

## 7. Run it as a service (systemd **user** unit + linger)

We have **no passwordless sudo**, so we can't drop a unit in `/etc/systemd/system`.
But the `arduino` user has **lingering enabled** (`loginctl show-user arduino` →
`Linger=yes`), so a **user** service runs at boot with no session and no root.

`~/.config/systemd/user/yeo-cell-bird.service`:
```ini
[Unit]
Description=Yeo cellular fake-bird-detection sender (UNO Q + A7670E -> cetools MQTT)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=%h/.yeo_cell.env
ExecStart=/usr/bin/python3 %h/cell_bird_sender.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
```
Install + start:
```sh
export XDG_RUNTIME_DIR=/run/user/$(id -u)
mkdir -p ~/.config/systemd/user
# (copy the unit above into ~/.config/systemd/user/yeo-cell-bird.service)
systemctl --user daemon-reload
systemctl --user enable --now yeo-cell-bird.service
systemctl --user status yeo-cell-bird.service
```
If linger were ever off: `loginctl enable-linger arduino` (needs root) — but it's
already on here.

> A system-unit variant (`User=arduino`, `WantedBy=multi-user.target`) is staged at
> `~/yeo-cell-bird.service` if you ever install it with sudo instead.

---

## 8. Tuning: cadence and radio-off

Two staged helper scripts (each edits `~/.yeo_cell.env` and restarts the service):
```sh
sh ~/set_cadence.sh <BATCH_SIZE> <GEN_INTERVAL>   # e.g. 5 180  (field)  |  1 60 (visible test)
sh ~/radio.sh <0|1>                                # 0 = radio stays attached | 1 = CFUN=0 between bursts
```

- **Field cadence (validated):** `BATCH_SIZE=5`, `GEN_INTERVAL=180` → a burst of 5
  every **~15 min**. Note the *first* burst after any restart is **~12 min** (the
  first detection spools at t=0, so only 4×180 s follow); steady state is 15 min.
- **Radio-off (validated over 4 cycles):** with `YEO_RADIO_OFF=1` the modem drops to
  `CFUN=0` after each burst and **re-attaches from cold on the next** — full window
  (re-register → bearer → MQTT → publish 5 → radio off) is only ~6–8 s, no failures,
  radio idle ~14 of every 15 min. Safe for the field. (Only unmeasured item is
  actual current draw — put a meter on the HAT if you want the number.)
- **Visible test cadence:** `1 60` (send each detection immediately, every 60 s) is
  handy when you want to *watch* messages land — but remember they're **not
  retained**, so a subscriber only sees them if connected at that instant (§10).

---

## 9. Verify

**Device side — watch the service log:**
```sh
export XDG_RUNTIME_DIR=/run/user/$(id -u)
journalctl --user -u yeo-cell-bird -f
# a healthy window looks like:
#   modem AT port auto-detected: /dev/ttyUSB1
#   registered on LTE (roaming), signal -71 dBm
#   data bearer up, IP 100.64.48.163
#   MQTT connected to tcp://mqtt.cetools.org:1884
#   published … species=…   (×5)
#   flush window done: 5 sent, 0 still queued
#   radio off (CFUN=0)        ← only if YEO_RADIO_OFF=1
```
Standalone modem health, any time:
```sh
python3 ~/cell_modem.py       # -> {"operator":"EE","act":"7","signal_dbm":-71,"reg":5,"attached":"1","ip":"…"}
```
Spool depth: `ls ~/.yeo_bird_spool/*.json | wc -l` (queued), `*.failed` (given up).

**Broker side — confirm it actually lands on cetools** (do this; `+CMQTTPUB: 0,0`
alone doesn't prove the ACL let it through). Keep a subscriber connected and wait
for the burst (messages aren't retained):
```sh
mosquitto_sub -h mqtt.cetools.org -p 1884 -u student -P '<password>' -t 'student/yeo/#' -v
# expect, e.g.:
# student/yeo/lora/yeo-unoq-4/up  {"device":"yeo-unoq-4","species":2441,"confidence":0.91,"hex":"09895b","transport":"cellular",…}
```
(A small paho subscriber does the same if you don't have `mosquitto_sub`.)

---

## 10. Gotchas we hit (the important part)

1. **ModemManager is the #1 cause of "cellular doesn't work."** It masquerades as
   SIM failure / registration denied / CGACT errors / "multiple access on port."
   **Mask it** (§3a). If it ever comes back (package update, re-enable), all those
   symptoms return.
2. **`+CME ERROR: SIM failure` is usually NOT the SIM.** With MM masked our SIM read
   `+CPIN: READY` instantly. Suspect contention/power before blaming the card.
3. **`AT+CGACT=1,1` returning `+CME ERROR: unknown error` does not mean "no data."**
   The network often pre-activates context 1 (its default bearer). The driver
   treats CGACT as non-fatal and checks `AT+CGPADDR=1` for a **real IP** instead.
4. **The AT-port number is not stable.** The A7670E exposes several `ttyUSB` nodes,
   more than one answers `AT`, and which is "ttyUSB2" vs "ttyUSB1" varies across
   reboots/resets. **Auto-detect** (`YEO_MODEM_PORT=auto`); never hard-code.
5. **PPP is impossible on this kernel** (no `ppp_generic`). Don't waste time on
   `pon`/chatscripts/`/dev/ppp`. Use the modem's AT MQTT stack. (`CellularSetup.md`.)
6. **Power brownout.** Modem on its own ≥2 A supply, board on mains, no shared 5 V.
   Under-powering shows as random Linux reboots / `No route to host`, not obvious
   modem errors.
7. **Force LTE.** `AT+CNMP=38`. On "auto", attaching to retired 3G gives
   `+CEREG/+CREG: 3` (denied). Roaming is normal and fine, not an error (stat 5):
   the current KeySIM/Tele2 roams onto **Vodafone-UK**; the earlier Giffgaff SIM
   came up on **O2/EE**.
8. **cetools `student` ACL.** Publish only under `student/#`, or it's silently
   dropped. Confirm with a subscriber, not just the modem's `+CMQTTPUB` (§5, §9).
9. **MQTT messages aren't retained.** A subscriber sees a burst only if connected at
   that instant — "I see nothing in MQTT" is usually just the ~15-min batch gap, not
   a fault. Check `journalctl` for `flush window done`.
10. **No passwordless sudo** (`<password>` is required for sudo). Hence the **user**
    systemd service + linger instead of a system unit. `systemctl --user` needs
    `XDG_RUNTIME_DIR=/run/user/$(id -u)`.
11. **Windows tooling quoting.** Inline `ssh 'a; b; $(c)'` breaks on `(`/`;`/MSYS
    path mangling. Use `.sh` files + `pscp`; set `XDG_RUNTIME_DIR=/run/user/1000`
    literally to avoid `$(id -u)` in inlined commands.
12. **`+CMQTTDISC/REL/STOP → ERROR` in the log after a failed connect is harmless
    noise** — you're tearing down a session that never came up. The driver now
    tracks session state so teardown only runs what's actually up.

---

## 11. Quick recreate (TL;DR)

```sh
# on the UNO Q (ssh arduino@<ip>, pass <password>):
sudo systemctl mask --now ModemManager                      # 1. kill the contention
python3 -m pip install --user --break-system-packages pyserial   # 2. driver dep
# 3. copy cell_modem.py, cell_bird_sender.py to ~/
# 4. write ~/.yeo_cell.env (see §5), chmod 600
# 5. sanity: modem + config
lsusb | grep -i simcom ; python3 ~/cell_modem.py            # -> status JSON w/ an IP
sh ~/run_cell_bird.sh --once                                # -> one detection to cetools
# 6. install the user service (see §7)
mkdir -p ~/.config/systemd/user
#   (drop yeo-cell-bird.service into ~/.config/systemd/user/)
export XDG_RUNTIME_DIR=/run/user/$(id -u)
systemctl --user daemon-reload
systemctl --user enable --now yeo-cell-bird.service
# 7. verify
journalctl --user -u yeo-cell-bird -f
mosquitto_sub -h mqtt.cetools.org -p 1884 -u student -P '<pass>' -t 'student/yeo/#' -v
```

---

## 12. State of play / open items (2026-07-15)

- ✅ `yeo-unoq-4` (hostname `dave`) publishing to `student/yeo/lora/yeo-unoq-4/up`
  over cellular; service enabled, linger'd, survives reboot; ModemManager masked.
- ✅ Field cadence (5 / 180 s, ~15 min bursts) and **radio-off between bursts** both
  validated — clean re-attach every cycle, 0 failures.
- ⬜ **Actual modem current draw** not yet measured (needs a power meter) — the only
  number left to quantify the radio-off saving.
- ⬜ Payload is a **simulated** detection. Swapping in the real acoupi/BirdNET
  inference output means having the inference process enqueue into the same spool
  (`~/.yeo_bird_spool`) or call `cell_modem.CellModem` directly — the driver is
  reusable for exactly this.
- Related: the handover overview in `../README.md`, the LoRa equivalent in
  `../2-la66-lora/UNO_Q_LA66_SETUP.md`, and the gateway/broker setup in
  `../3-gateway/README.md`.
