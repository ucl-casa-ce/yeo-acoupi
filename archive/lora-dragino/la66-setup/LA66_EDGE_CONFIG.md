# LA66 Edge Device — Configuration Runbook (single-channel → LORIOT)

Self-contained runbook for the UNO Q + Dragino LA66 edge node that ticks a
LoRaWAN uplink to LORIOT through a single-channel gateway, autonomously on power.
Companion docs (deeper background): `UNO_Q_LA66_SETUP.md` (first-principles setup).

## 1. Goal & Result

- **Target**: UNO Q + Dragino LA66 that, on power alone (no WiFi on the edge
  side), ticks a LoRaWAN uplink every 3 minutes to LORIOT via a single-channel
  ESP gateway.
- **Result**: DONE and verified end-to-end. The LA66 transmits real uplinks on
  868.100 MHz / SF12 (single channel), unconfirmed, FPort 2, every 180 s, with a
  tick counter persisted across restarts. The app auto-starts on boot; a physical
  power-cycle confirmed the whole chain comes up and reaches LORIOT with **zero
  manual intervention** (see §8).

## 2. How a tick travels (end-to-end message flow)

**Why it's not simpler:** the UNO Q is *one board with two processors* — a
Qualcomm **Linux** side and an **STM32U585 MCU**. **Every Arduino header pin,
including D0/D1 where the LA66's UART lands, belongs to the STM32**, so Linux
*cannot* open the LA66 serial port directly. A tick therefore has to hop Linux →
MCU → LA66 before it ever hits the air. The MCU relays it via the **Arduino
Bridge** (an RPC channel carried by the `arduino-router` service).

```
[Linux side — main.py ticker]
   │  builds an AT line, calls Bridge.call("la66_send", "AT+SENDB=00,02,7,AC51..")
   ▼  RPC frame over arduino-router's transparent tunnel (localhost :7500 → MCU's Serial)
[STM32 MCU — la66_bridge.ino (Bridge)]
   │  Bridge.provide("la66_send") writes that AT line out
   ▼  Serial1 = pins D0/D1 = USART1, 9600 8N1  (the physical 2-wire UART to the shield)
[Dragino LA66 shield]
   │  runs the LoRaWAN MAC, encrypts + frames the uplink, keys the radio
   ▼  RF — 868.100 MHz, SF12, unconfirmed, FPort 2
[Single-channel ESP gateway — listens ONLY on 868.1 MHz]
   │  wraps the frame as a Semtech UDP packet-forwarder datagram
   ▼  over the gateway's own WiFi → eu1.loriot.io:1780
[LORIOT network server (eu1)]
   │  matches the ABP session (DevAddr 1E6E6C89), de-dups, decrypts the payload
   ▼  application WebSocket "Output"
[loriot_ws_decode.py on a PC]  → prints the decoded tick
```

- **Linux → MCU** uses the Bridge RPC (`la66_send` / `la66_drain`), *not* a raw
  socket — see §3 for why.
- **MCU → LA66** is the plain 2-wire 3.3 V UART on D0/D1 (LA66 `TXD→D0`,
  `RXD→D1`), 9600 8N1, on `Serial1`.
- **LA66 → gateway** is ordinary LoRaWAN RF; the LA66 uses its **factory ABP
  session** (DevEUI `A840414A655D113C`, DevAddr `1E6E6C89`; the secret
  NwkSKey/AppSKey live in `LA66_HANDOFF.md §3` / `Loriot Set up for first.txt` —
  not reproduced here). This same session is registered in LORIOT as device
  **UnoQD**.
- **gateway → LORIOT** needs the gateway pointed at LORIOT's Semtech-UDP port
  **1780** (not TTN's 1700) — that one-line gateway fix is what actually made
  "routing to LORIOT" work; the gateway is configured separately (ESP
  `ESP_sc_gway_end` firmware).

## 3. Architecture decision — and what was deliberately *not* done

**Chosen: the Bridge/RPC path (framework-based).**

**Why not the raw `:7500` socket path.** `:7500` is `arduino-router`'s
transparent byte tunnel to the MCU's `Serial`; a "pass-through" sketch plus a
Linux script (`lora.py`) can drive the LA66 over it directly. But **while the
Arduino App is running, the framework keeps its *own* connection to that same
tunnel and steals bytes** from the script: big reads (`AT+CFG`) come back
garbled, some reads return nothing, and only short sends (`AT+SENDB`→`txDone`)
*sometimes* slip through. It's a race, not a reliable link. The Bridge RPC is the
framework's intended IPC, so it never contends — hence it's the verified path.

**Why not the "framework-free" image.** The handoff's stretch goal was to strip
the framework (keep only `arduino-router`) and run the ticker as a bare systemd
service, for a RAM-frugal image. Two reasons we didn't:
1. **RAM isn't the constraint** — ~2.9 GB free with the full framework +
   desktop; the framework is only ~270 MB. The optimization buys little.
2. It **depended on an unresolved question**: does the STM32 run its flashed
   sketch on a *cold power-on without the App*? (`app stop` halts the MCU; a warm
   reboot doesn't bring it up; only `app start` = OpenOCD flash-and-run does.) The
   Bridge + default-app-autostart route **sidesteps that entirely**, because the
   autostart *is* an `app start`, which does the OpenOCD reset-run that boots the
   MCU. We never had to answer the cold-boot question — and the power-cycle test
   (§8) then proved the autostart route works.

**How autonomy is achieved:** the app is set as the framework **default app**
(`properties set default`); on boot the `arduino-app-cli` daemon starts it, which
flashes+runs the MCU and launches the ticker. No WiFi, no login, no manual step.

**Two on-board files** (working copies saved in `LA66/edge_app/`):
- STM32 **Bridge sketch** `la66_bridge.ino` — exposes `la66_send` / `la66_drain`.
  (Deploys as `sketch/sketch.ino` on the board — see §6.)
- Linux **ticker** `main.py` — drives the LA66 through those RPC calls.

## 4. Board access

- **adb over USB**: device serial `2354301559` (hostname `yeo-unoq-3`, user
  `arduino`). All commands via `adb -s 2354301559 shell '...'`.
- **`.local`/mDNS does not resolve** on the managed WiFi here — use adb over USB
  (or the router's DHCP list for the IP), never `ssh yeo-unoq-3.local`.
- **No remote reboot**: `adb reboot` is refused by adbd, and `sudo` needs a
  password over adb (no tty). The board can only be rebooted by a **physical
  power-cycle** (unplug/replug USB, or the 5 V/3 A brick). This is why the
  autostart proof required a human hand.

## 5. The LA66 config sequence

The single-channel gateway only listens on 868.1 MHz / SF12, so the LA66 must be
pinned there. The ticker sends, on startup, in order:

| Cmd | Purpose | Observed reply |
|---|---|---|
| `AT` | Warm-up (first cmd after boot often errors) | `AT_ERROR` or empty (expected) |
| `AT+ADR=0` | Disable adaptive data rate (so a fixed DR/channel sticks) | `OK` |
| `AT+DR=0` | DR0 = SF12 (max range, matches gateway) | `Attention:Take effect after AT+ADR=0` then `OK` |
| `AT+CHS=868100000` | Set single-channel frequency 868.1 MHz | `OK` |
| `ATZ` | **Reset the module so AT+CHS takes effect** | Module reboots: "Dragino LA66 ... DevEui= A8 40 41 4A 65 5D 11 3C ... JOINED" |

**KEY GOTCHA — `AT+CHS` needs a follow-up `ATZ`.** Setting `AT+CHS` alone does
NOT switch the LA66 to single-channel mode — it kept hopping (observed TX on
868.5 and 868.3 MHz even while `AT+CHS=?` read back `868100000`). Only after
`ATZ` did it lock to 868.100 MHz. **The `ATZ` reset is mandatory.**

**Verified after ATZ:** `AT+CHS=?` → `868100000`, `AT+DR=?` → `0`, `AT+ADR=?` →
`0`, and consecutive ticks log "TX on freq 868.100 MHz at DR 0 ... txDone".

**Uplink command per tick:** `AT+SENDB=00,02,7,<payload>`
- `00` = **unconfirmed** — a single-channel gateway cannot send the downlink ACK
  a confirmed frame needs, so confirmed uplinks would just time out.
- `02` = FPort 2 · `7` = 7 payload bytes.
- Payload (7 bytes): magic `0xAC` · tag `0x51` ('Q') · tick (uint32 LE) · site (1
  byte). Example (tick=3): `AC510300000000`.

## 6. Deploy steps

Push the two files to the board (previous pass-through sketch + inert main.py are
backed up on-board as `~/sketch.ino.passthrough.bak` / `~/main.py.inert.bak`):

```bash
# la66_bridge.ino deploys AS sketch/sketch.ino: Arduino requires the primary .ino
# to match its sketch/ folder, so the on-board name must stay sketch.ino.
adb -s 2354301559 push la66_bridge.ino /home/arduino/ArduinoApps/la66/sketch/sketch.ino
adb -s 2354301559 push main.py         /home/arduino/ArduinoApps/la66/python/main.py
```

Compile the sketch, flash the STM32 via OpenOCD, and run the Python ticker:

```bash
adb -s 2354301559 shell 'arduino-app-cli app start user:la66'
adb -s 2354301559 shell 'arduino-app-cli app logs  user:la66'   # watch: cfg replies, TX on 868.1, txDone
```

Arm autostart on boot (the daemon starts the default app, which does the OpenOCD
reset-run that brings the MCU up):

```bash
adb -s 2354301559 shell 'arduino-app-cli properties set default user:la66'
adb -s 2354301559 shell 'arduino-app-cli properties get default'   # -> Default app: la66
```

## 7. Ticker behaviour (main.py)

- Runs as the App's Python side (`from arduino.app_utils import *`); drives the
  LA66 **only** via `Bridge.call("la66_send"/"la66_drain")` — no raw socket, so no
  contention with the framework (§3).
- **On startup**: configures per §5 (including the `ATZ`), prints read-backs,
  then sends the first tick immediately and every `INTERVAL = 180 s` after.
- **Tick counter** persisted in `~/.yeo_tick` (atomic write); advances only on a
  successful send. (Note: this is a soft counter for the payload — the LoRaWAN
  FCnt is separate and resets on boot, see §9.)

## 8. Verification (all PROVEN this session)

1. **Autostart on a real power-cycle — ✓.** Board physically power-cycled
   (`boot_id` changed `a82bc828…` → `62f33b7b…`). With zero manual steps the
   framework auto-started `user:la66`, flashed+ran the STM32 via OpenOCD, and the
   ticker configured the LA66 (`ATZ`) and sent `tick 1`:
   `UpLinkCounter= 0 ... TX on freq 868.100 MHz at DR 0 ... txDone`.

2. **End-to-end reception in LORIOT — ✓.** Ran `loriot_ws_decode.py` against the
   LORIOT app WebSocket; a live uplink arrived:
   ```
   {"cmd":"rx","EUI":"A840414A655D113C","fcnt":1,"port":2,"freq":868100000,
    "rssi":-31,"snr":12,"dr":"SF12 BW125 4/5","devaddr":"1E6E6C89","data":"ac510200000000",
    "gws":[{"gweui":"08A6F7F4F40D21AC"}]}
   ```
   Payload `ac51 02 00000000` = tick 2, on 868.1 MHz / SF12 / FPort 2, received by
   the single-channel gateway `08A6F7F4F40D21AC` and accepted by LORIOT. This was
   a **post-reboot** uplink (frame counter reset to `fcnt 1`), so it proves both
   the autonomous boot *and* that LORIOT tolerates the counter reset. (Earlier in
   the session tick 8 / fcnt 5 was also observed, confirming steady-state.)

## 9. Caveats

- **LoRaWAN frame counter resets on every boot/ATZ.** After `ATZ` the LA66's
  FCnt restarts at 0. **Empirically OK here** — the LORIOT `UnoQD` device accepted
  the post-reboot `fcnt 1` uplink, so its frame-counter validation already
  tolerates resets. Keep that setting; if it's ever tightened to strict
  monotonic, post-power-cycle uplinks would be dropped as replays until the count
  climbs back.
- **Unconfirmed uplinks only** — single-channel gateway can't return the downlink
  ACK.
- **No WiFi in the edge data path** — LA66→gateway is RF and the ticker runs
  locally on the UNO Q. WiFi was only ever for adb/ssh. (The *gateway* uses its
  own WiFi to reach LORIOT — that's on the gateway, not the node.)
- **Optional fallback (not needed):** `edge_app/yeo-lora-ticker.service` is an
  idempotent systemd unit that starts the app on boot if the default-app autostart
  ever fails. Install with `sudo` only if needed; the default-app mechanism worked.
- **Revert to the raw/pass-through path:** restore `~/sketch.ino.passthrough.bak`
  + the inert main.py, then `app start` (see `LA66_HANDOFF.md §8`).

---

**Status: COMPLETE** — the device autonomously boots on power, ticks 868.1/SF12,
and uplinks reach LORIOT (verified end-to-end after a real power-cycle). No manual
intervention, no WiFi on the edge side.
