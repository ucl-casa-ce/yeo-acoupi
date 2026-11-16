# Yeo Valley bioacoustics

Solar-powered Arduino UNO Q edge devices running acoupi with BatDetect2 and
BirdNET. They detect bats and birds, and send detection metadata only —
never audio — to `mqtt.cetools.org` over LoRaWAN or cellular.

## Where things stand

| Piece | State |
|---|---|
| Detection pipeline on the UNO Q | Installed, 13/13 tests pass, 192 kHz AudioMoth capture verified |
| Cellular link (A7670E) | Works standalone, sending simulated detections |
| LoRa link (LA66) | Works standalone, sending simulated detections |
| Gateway | MultiTech setup with Network Server |
| acoupi → broker end-to-end | Never observed. Recording is tested but no live detection yet → message to broker is not built |

---

## 1-cellular — A7670E cellular comms

**Current SIM: KeySIM (Tele2), APN `key`, roaming on Vodafone-UK.** An earlier
Giffgaff SIM (APN `giffgaff.com`, O2) was used first and still appears in places
in the runbook and as a code default — check the APN matches the SIM in the
device before blaming the radio. A mismatch gives `AT+CEREG? → 0,3` (data
registration *denied*) with perfectly good signal, and needs
`AT+CGDCONT=1,"IP","key"` then `AT+CFUN=1,1` to re-attach.

- `UNO_Q_CELLULAR_SETUP.md` — bare board to publishing over 4G, every step used
  to get the `yeo-unoq-4` node live, with the gotchas. Written against the
  Giffgaff SIM; the sequence is identical for KeySIM with the APN swapped.
- `code/cell_modem.py` — the reusable interface layer. All AT/serial/MQTT
  mechanics: port auto-detect, `bring_up()` (SIM → LTE-only → registration → APN
  → PDP → IP), MQTT connect/publish/disconnect, `radio_off()`, typed errors.
  **This is what an acoupi messenger should wrap** — its docstring says so.
- `code/cell_bird_sender.py` — the working application. Generates simulated
  detections, spools to disk, flushes over one cellular window, deletes only on
  confirmed `+CMQTTPUB`. Shows the modem lifecycle production should reuse.
  Its disk spool is *not* needed for acoupi, which has its own message store.

Watch for: ModemManager grabs the AT port. `cell_modem.py` detects it but can't
prevent it.

## 2-la66-lora — LA66 LoRaWAN comms

**Current path: LA66 → MultiTech Conduit built-in Network Server → local
mosquitto → MQTT bridge → `mqtt.cetools.org`.** The gateway half is section 3.
TTN and Loriot appear throughout these docs because they were the earlier
routes — where you see them, the Conduit NS has replaced them.

- `UNO_Q_LA66_SETUP.md` — the device-side chain, UNO Q Linux side → LA66 → on
  air. **§11 is the current setup** ("Pointing the LA66 at the MultiTech built-in
  Network Server", ABP → OTAA); §11e covers the cetools downstream. §§1–7
  (hardware, OS, shell, app, AT reference, gotchas) apply whatever the network
  server. §8 (TTN), §9 (TTN bridge) and §10 (Loriot) are the earlier progression,
  superseded by §11.
- `code/` — the current driver kit. `lorawan.py` is the latest (16 Jul),
  superseding the older `lora.py`: it adds a transport seam (`SocketLink` /
  `SerialLink`) and OTAA join/provisioning on top of the field-proven duty-cycle
  pacing, reconnect and crash-durable spool.
  - `example_1_simple.py` — send one message. Start here.
  - `example_2_robust.py` — keys, join, send, error handling.
  - `example_3_buffered.py` — the shipping pattern: disk queue + background
    worker.
  - `test_lorawan.py` — runs the whole stack against a **fake radio**, so you can
    prove it works with no hardware.
  - `bird_sender.py` — simulated detections as 3-byte uplinks (species uint16 +
    confidence uint8), byte-identical to the cellular records. Sending code is
    network-agnostic; its docstring and the `decodeUplink()` JavaScript at the
    bottom describe the old TTN downstream — see the handover note at the top of
    the file. **Its import was also ported from `lora` to `lorawan` for this pack
    and has not been run since** — the three calls it makes have identical
    signatures in both, but check it before trusting it.

  The driver is network-agnostic — `lorawan.py` says so explicitly ("a device can
  be pointed at LORIOT, TTN, or a custom network server"). OTAA against the
  Conduit's local join server needs no code change, just the DevEUI and AppKey
  registered under LoRaWAN → Key Management on the gateway.
- `edge/` — the MCU side: `la66_bridge.ino` pass-through sketch, `main.py`, and
  the systemd unit. Needed to reproduce the working setup.
- `LA66_RPC_VS_RAW.md` — decision record. Built twice, RPC first, then raw
  socket. Read before changing the transport, or you'll rebuild the abandoned
  approach.
- `LA66_HANDOFF.md` — **known open defect**: the raw-socket path works only
  while the Arduino App is started, which couples it to the ~270 MB framework and
  causes intermittent `:7500` failures.

Duty cycle is the load-bearing constraint: EU868 at SF12 needs ~131 s off-air, so
naive 60 s sending isn't legal. `lorawan.py` enforces this.

## 3-gateway — MultiTech Conduit as network server

- `README.md` — configuring the Conduit as its own LoRaWAN Network
  Server, decoding on the gateway, publishing to a local mosquitto, and bridging
  to `mqtt.cetools.org`. Verified on MTCDTIP-L4E1, mPower 6.0.1, LoRa NS 2.6.8,
  two MTAC-LORA-H-868 cards.
- The architectural constraint is here: **Packet Forwarder vs Network Server is
  a whole-gateway setting, not per-card.** In Packet Forwarder mode both cards can
  feed Loriot and TTN simultaneously; choosing the built-in NS switches the whole
  box out of forwarding. To have decoded MQTT *and* raw forwarding, run the
  network server off-gateway.
- Uplink `data` is base64 raw payload — decrypted but not field-decoded. Turning
  it into `{species, confidence}` happens downstream.

## 4-unoq — the edge device

### a-board-setup — get the board working
- `UnoQSetup.md` — from-scratch build runbook, Parts 1–10, from flashing onward.
- `UNO-Q-Quirks-and-Gotchas.md` — hard-won bring-up notes for the dual-brain
  board (Qualcomm Linux MPU + STM32 MCU), written after a multi-day debugging
  slog. Read this before concluding a board is dead.

### b-acoupi — get acoupi working
- `ACOUPI_UNOQ_DEPLOY.md` — install plus the five adaptations the UNO Q needs:
  CPU-only torch (a plain `uv sync` pulls ~2.3 GB of unusable CUDA wheels), the
  PipeWire recorder shim, device-enumeration bypass, config field types, and the
  file-cleanup guard. **Start here.**
- `AUDIOMOTH_UNOQ.md` — finding the PipeWire node name, verifying 192 kHz
  capture, wiring it in. `device_name` must be the full node name; a friendly
  label silently falls back to the default source.
- `ACOUPI_MESSAGING_MODES.md` — the three ways to shape what gets sent: one
  message per recording (built, currently running), batched individual
  detections (needs ~20 lines), and summarised counts (components exist, needs
  wiring). Recommended: bats summarised, birds batched individually.
- `ACOUPI_CELLULAR_PLAN.md` — the design for joining acoupi to the modem, and
  the decisions still open.
- `code/` — the amended source: `pyproject.toml`, `recorder.py` (new),
  `program.py`, `config.py`, `models.py`. First move should be `uv sync` then `pytest` (expect 13 passed).

Test-only settings to revert, marked `# TEST` in the source: all-day bat window
(production is 18:00–06:00), 15 s send interval, per-detection message
factories, 30 s heartbeat.

## 5-data-budget — SIM planning

- `CELLULAR_DATA_BUDGET.md` — payload sizes by encoding, bit-stacking options
  with numbers, session overhead, usage by batching cadence, 1NCE vs KeySIM.
- `Cellular-Data-Calculator.xlsx` — live calculator. Change detections/day,
  record sizes, overhead or cadence and everything updates.

Two conclusions drive the design: **~92% of hourly traffic is connection
overhead**, so cadence sets the bill rather than payload format; and moving from
hourly to a single daily push saves ~157 MB per device over ten years while
cutting radio-on time from ~109 to ~4.6 hours a year.

**KeySIM is what's in the device today** and what the cellular link was proven
on. The doc's tariff comparison is forward-looking for the fleet rollout: on
these volumes 1NCE (€12/SIM, 500 MB, 10 years) works out roughly 30× cheaper than
KeySIM's ~£3/SIM/month rental, because the rental dominates when you send this
little data.

## 6-visualisation 

A single html file with some clickable markers as an example of a simple map-based dashboard.

---

## Not included, deliberately

Bench test nodes (`lorawan/heltec`, `mkrwan_*`, `mkr_bird_*`), the TTN MQTT
bridge, one-off probe scripts, the mioty experiment, and archived duplicates.

## Known gaps/todos

- **acoupi ↔ radio integration** — the main unbuilt piece.
- **No end-to-end detection** has reached the broker.
- **SD-card retention not built** — recordings are deleted after processing
  (`file_managers=[]`). 1 TB card assumed; ~2 GB/day at 192 kHz.
- **Gateway operational details** (IP, SSH access, the UDP 1780 firewall block,
  log paths) aren't written down anywhere. Note the gateway now runs in **Network
  Server** mode — the earlier dual-card Loriot + TTN packet-forwarding
  arrangement is historical, and the two modes are mutually exclusive.
- **Four acoupi compatibility items** worth noting — `pw-record
  --sample-count` (PipeWire ≥1.5 only), `pw-dump` rate parsing on multi-rate USB
  mics, and `Path | None` / `datetime.time` config fields having no parser
  handler. Every Debian-stable deployment will hit these.
