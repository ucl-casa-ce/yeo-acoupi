# Plan — acoupi bat/bird detections over cellular to CeTools MQTT

**Status: proposal. Nothing in here is built.** Written 2026-07-17 against the
verified install on `unoq-yeo-5` (see `ACOUPI_UNOQ_DEPLOY.md`).

## The shape of it

- **30 s duty cycle:** record 3 s → process detections in the remaining ~27 s →
  repeat. No transmission in this window.
- **Audio stays on the device.** Recordings are logged to SD card, never sent.
- **Detections are queued locally and sent in batches, ~hourly**, to
  `mqtt.cetools.org:1884`.
- **Payload is detection metadata only:** either a per-species count, or
  individual detections (species, confidence, timestamp).
- Transport reuses the existing `cell_modem.py` AT interface.

---

## 1. The good news: the architecture already fits

Three things mean this is mostly **configuration and one component**, not a new
pipeline:

1. **`acoupi` already does store-and-forward.** `program.py` writes detections to
   a `SqliteMessageStore` (`~/messages.db`); a separate `send_messages` task
   drains it via a `Messenger` on its own schedule. That decoupling is exactly the
   "process every 30 s, send hourly" split. **Do not port `cell_bird_sender.py`'s
   disk spool** — acoupi's message store already is it.

2. **`cell_modem.py` was explicitly built for this.** Its docstring says the split
   from `cell_bird_sender.py` exists so "a heartbeat, a bat sender, or a real
   acoupi messenger can reuse cell_modem without copying AT-command handling."

3. **Hourly batching means the existing modem lifecycle is correct as-is.**
   `bring_up → mqtt_connect → publish → disconnect → radio_off` per flush window
   is designed for exactly this cadence, and `YEO_RADIO_OFF=1` keeps the solar
   power budget intact. No persistent session, no re-architecture.

`acoupi.components.types.Messenger` is an ABC with one abstract method:

```python
def send_message(self, message: data.Message) -> data.Response: ...
```

---

## 2. Verified timings (measured on-board, 2026-07-17)

| Model | Clip | Cold (load + infer) | Warm inference | Detections |
|---|---|---|---|---|
| BatDetect2 | 2.0 s @ 384 kHz | 39.3 s | 3.0 / **3.4** / 4.0 s | 62 |
| BirdNET | 3.0 s @ 44.1 kHz | 20.4 s | 3.0 / **3.1** / 3.1 s | 0 |

**The 27 s processing budget is comfortable** — one model per cycle is ~3–5 s
(the bat clip was 2.0 s; a 3 s clip should scale to ~5 s). Both models on one clip
would be ~6.4 s.

**Cold start is ~60 s combined.** Models must load once into a long-lived celery
worker and stay resident; any per-cycle process restart breaks the cycle. acoupi's
worker model already does this — don't undermine it.

---

## 3. The dominant design decision: counts vs individual detections

**62 detections came from a single 2 s bat clip** at `detection_threshold=0.3`.
Extrapolated at one bat clip per 60 s, a dense night could produce **~3,600
detections/hour**. This decides the transport design, so settle it first:

**Option A — counts/summaries (recommended).** One aggregated message per hour.
The machinery exists: `DetectionCountByTagSummary` + the hourly
`generate_summaries` crontab task in `program.py`. Payload is a few hundred bytes
per hour. Trivial data cost, trivial airtime.

**Option B — individual detections.** ~3,600 records/hour × ~100 bytes ≈ **360
kB/hour ≈ 8.6 MB/day/device**, across the fleet. Affordable-ish, but
there is a **hard mechanical problem**:

> acoupi's `send_messages` task calls `send_message()` **once per message**, and
> each `cell_modem.mqtt_publish()` is a full AT transaction
> (`CMQTTTOPIC` → `CMQTTPAYLOAD` → `CMQTTPUB`) at QoS 1. At ~1–2 s per publish,
> 3,600 individual publishes **cannot fit in an hour**. This is the same issue
> already visible on `yeo-uno-4`, where a 422-record spool would flush as 422
> sequential round-trips.

So Option B requires **coalescing many detections into one MQTT payload** (a JSON
array per publish), which means either a batching messenger that aggregates before
publishing, or writing aggregated messages into the message store in the first
place. That is real design work; Option A avoids it entirely.

**A hybrid is likely what you want:** hourly counts as the baseline, plus
individual detail only for species of interest above a higher confidence
threshold — bounding the record count while keeping the detail that matters.

---

## 4. Gap: recordings are currently deleted, not logged

This is not built and is a prerequisite for "log the recording on the SD card".

`program.py` today:

```python
tasks.generate_file_management_task(
    store=self.store,
    file_managers=[],  # No file managers means delete everything
    management_conditions=[has_been_processed],
)
```

Recordings go to a **temp dir** (`get_temp_dir()`, i.e. shared memory) and are
deleted once processed. To retain them you need:

1. **SD storage.** Confirm the device has an SD card and its mount path. Note
   `/home/arduino` is an 18 GB **eMMC** partition (`/dev/mmcblk0p69`) — that is
   internal storage, *not* an SD card. Whether an SD slot is present/populated on
   these boards is an open question.
2. **A file manager** (e.g. acoupi's save-recording managers) writing to that
   path, replacing `file_managers=[]`.
3. **A retention policy.** 3 s @ 384 kHz mono 16-bit ≈ 2.3 MB per bat clip; at one
   per minute that is **~3.3 GB/day**. Bird clips at 44.1 kHz are ~265 kB (~380
   MB/day). Retention, rotation, and card capacity need sizing — this dwarfs every
   other storage concern in this plan.
4. **A recovery story.** Cards fill, cards fail; decide what happens when the SD
   is full or absent (keep detecting and drop audio, or stop?).

---

## 5. Hardware needed (none currently attached)

| Item | Status | Note |
|---|---|---|
| Waveshare A7670E modem | **Not attached** to `unoq-yeo-5` (no `/dev/ttyUSB*`) | Lives with the `yeo-uno-4` work |
| KeySIM (Tele2), APN `key` | Config known | Current SIM. An earlier Giffgaff SIM used APN `giffgaff.com`; a NetworkManager profile literally named `giffgaff` is still saved on the board |
| Ultrasonic mic (384 kHz) for bats | **Not attached** | Onboard Imola codec is not a substitute |
| Bird mic (44.1/48 kHz) | **Not attached** | |
| SD card | **Unconfirmed** | See §4 |

**USB contention is a real constraint.** The board's USB-C port carries ADB, so
mic and modem cannot be attached while developing over that cable. Settle how dev
access works on a populated device — SSH over WiFi (the board is reachable on
`nextguest`), or a powered hub — **before** any on-device mic/modem testing is
meaningful.

---

## 6. What needs installing / setting up

### 6.1 On the board

1. **Copy `cell_modem.py`** from `yeo-uno-4` (ADB serial `1603135366`,
   `~/cell_modem.py`, 555 lines) into the package —
   `src/acoupi_yeo_valley/cell_modem.py` — so it ships with the project rather
   than floating in `$HOME`. **Only `cell_modem.py`**; not `cell_bird_sender.py`
   (that's the fake-detection app, which acoupi replaces).

2. **Add `pyserial` to `pyproject.toml`.** `cell_modem.py` needs it and it is
   **not** among the installed 163 packages. Re-lock afterwards.

3. **Free the AT port from ModemManager.** `modemmanager` 1.24.0 is installed and
   active and grabs `/dev/ttyUSB*`. `cell_modem.py` detects this
   (`modemmanager_active()`, `ModemBusy`) but cannot prevent it. Also disable the
   saved `giffgaff` GSM NetworkManager profile, which would fight for the same
   modem. Mask ModemManager, or udev-blacklist the A7670E AT port.

   > Deliberate trade-off: if you ever wanted ModemManager/PPP to provide an *IP*
   > link, acoupi's stock `MQTTMessenger` would work unchanged and none of §6.2
   > would be needed. This plan follows the instruction to reuse `cell_modem.py`,
   > which makes the AT path and ModemManager mutually exclusive.

### 6.2 Code to write

1. **`CellularMQTTMessenger(types.Messenger)`** in `components.py`, wrapping
   `cell_modem.CellModem`:
   - `send_message(message) -> data.Response`; confirmed `+CMQTTPUB` →
     `ResponseStatus.SUCCESS`, `ModemError` → `FAILED` so acoupi retains unsent
     messages for the next window
   - **must never raise** on modem-absent — return a failed `Response` (mirrors
     `flush_spool()`'s existing "never raise" contract)
   - `check()` → `bring_up()`/`status()` for the program's `check()` hook
   - opens the modem for the flush window and closes + `radio_off` after (§1.3)

2. **`CellularConfig`** in `config.py` (port/baud/APN/LTE-only/broker/topic/
   client-id/QoS), replacing `messages: MQTTConfig`. Reuse the proven values from
   `~/.yeo_cell.env` on `yeo-uno-4`: `mqtt.cetools.org:1884`, user `student`,
   QoS 1, LTE-only. **Set `YEO_APN=key` for the current KeySIM** — the env file on
   that board still held the older `giffgaff.com`.

3. **`program.py` changes:**
   - `self.messenger = CellularMQTTMessenger.from_config(config.messages)`
   - **bird recording duration 10 s → 3 s** (`BirdRecordingConfig.duration`)
   - **`send_messages` schedule 5 min → hourly**, so one modem window per hour
   - **heartbeat 30 s → hourly.** Currently every 30 s with the hourly line
     commented out — a debug leftover; over cellular it would dominate traffic
   - file management → retain to SD (§4)

4. **Topic/device identity.** `yeo-uno-4` publishes to
   `student/yeo/lora/yeo-unoq-4/up`. A cellular scheme needs a per-device topic
   that isn't under `.../lora/...` and isn't derived from hostnames (unreliable on
   these boards — see the deploy doc).

---

## 7. Cycle interpretation — needs your call

The existing program **already runs a 30 s cadence**: bats at `:00` (3 s, offset
0) and birds at `:30` (10 s, offset 30), each on a 60 s schedule. Changing bird
duration to 3 s makes each 30 s slot exactly "record 3 s → ~27 s to process".
Each species is then sampled every 60 s and only one model runs per cycle (~3–5 s).

The alternative — **both** models every 30 s — needs one 384 kHz recording
resampled for BirdNET, since acoupi routes to a model by samplerate
(`has_been_processed`, `>90_000`). More code, ~6.4 s compute, double the sampling
rate per species, double the SD burn.

**Recommendation: the alternating reading** — matches your words, matches existing
code, halves per-cycle compute and storage.

---

## 8. Suggested build order

1. Settle §3 (counts vs individual) and §7 (cycle) — both change what gets built.
2. Confirm SD presence/mount and retention policy (§4).
3. Resolve USB/dev access (§5) — otherwise nothing on-device is testable.
4. Copy `cell_modem.py` into the package; add `pyserial`; re-lock.
5. Neutralise ModemManager + the `giffgaff` NM profile.
6. Write `CellularMQTTMessenger` + `CellularConfig`; unit-test against a fake
   modem — no hardware needed, `tests/` already has this shape.
7. With the A7670E attached and acoupi off, measure real `bring_up` + publish
   latency to size the hourly window honestly.
8. Swap into `program.py`; adjust schedules; add the SD file manager.
9. Verify at the broker: subscribe to the device topic on `mqtt.cetools.org`.

---

## 9. Open questions

- **Counts, individual detections, or hybrid?** (§3 — decides the transport.)
- Is there an SD card on these boards, and what is the retention policy for
  ~3.3 GB/day of ultrasonic audio? (§4)
- Which board is the cellular acoupi target — `unoq-yeo-5` (has acoupi, no modem)
  or `yeo-uno-4` (has modem + cell code, and its own acoupi services)?
- Per-device topic scheme for the cellular path?
- Does the ultrasonic mic coexist with the modem on available USB ports?
- Is 3 s enough for BirdNET? It natively windows at 3 s, so a 3 s clip is exactly
  one window — worth confirming that isn't a degenerate case.
