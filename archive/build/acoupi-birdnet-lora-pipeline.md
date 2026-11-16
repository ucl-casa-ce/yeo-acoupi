# acoupi BirdNET → LoRa pipeline on Uno Q (Part 2)

Continues from [`acoupi-birdnet-unoq.md`](acoupi-birdnet-unoq.md), which ends right
before `acoupi setup`. This part covers setup, the **required source patches** to run
acoupi on the (non-Raspberry-Pi) Uno Q, the **LoRa sidecar**, launching, and verifying a
real detection reaching LORIOT.

> **Reminder of where this sits:** LoRa is the *test* output (the gateway was already up).
> Production is meant to be static WiFi → MQTT — see the messaging toggle in
> `acoupi-birdnet-unoq.md` §B. The acoupi MQTT messenger is already configured alongside
> the LoRa path, so switching later is config-only.

---

## Phase 5 — `acoupi setup`

```bash
# venv must be active, or call acoupi by full path:
/home/arduino/bioacoustics/.venv/bin/acoupi setup --program acoupi_birdnet.program
```

Answers we used:

| Prompt | Answer |
|---|---|
| timezone | `Europe/London` |
| audio device | **`192kHz AudioMoth USB Microphone`** (index 2 in the list) |
| channels | `1` |
| **samplerate** | `192000` (see gotcha) |
| time expansion | `1.0` |
| recording.duration | `3` |
| recording.interval | `10` |
| recording.schedule_start / end | `06:00` / `22:30` (we later widened start to 04:00) |
| paths.tmp_audio | `/run/shm` |
| paths.recordings | `/home/arduino/bioacoustics/data/recordings` |
| paths.db_metadata | `/home/arduino/bioacoustics/data/metadata.db` |
| messaging.messages_db | `/home/arduino/bioacoustics/data/messages.db` |
| messaging.message_send_interval | `120` |
| http.base_url | (set to the sidecar URL later — see Phase 6) |
| mqtt.* | host `mqtt.cetools.org`, topic `yeo/unoq-bird/acoupi`, port `1884` (production path) |
| deployment name / lat / lon | prompted on `deployment start`, e.g. `mfbird1`, `51.562352`, `0.020055` |

> **Gotchas:**
> - At a `[Y/n]` prompt, typing a *value* (e.g. a path) errors with `invalid input`.
>   Answer **`n`**, and it then prompts *"Please provide a value…"* where you type it.
> - **Samplerate stays 192000.** PortAudio only advertises the AudioMoth's native rate, so
>   `48000` is rejected. acoupi resamples to 48 kHz internally for BirdNET — fine, just
>   bigger temp WAVs (~1.15 MB / 3 s clip).
> - `acoupi setup` writes `~/.acoupi/config/program.json`. We tune it next with `jq`.

---

## Phase 6 — config corrections (`jq`)

```bash
cd ~/.acoupi/config && cp program.json program.json.bak
jq '
    .messaging.http.base_url            = "http://127.0.0.1:8000"
  | .recording.schedule_start           = "04:00:00"
  | .saving_filters.starttime           = "04:00:00"
  | .saving_filters.endtime             = "22:30:00"
  | .model.detection_threshold          = 0.7
  | .detections.threshold               = 0.7
  | .saving_managers.saving_threshold   = 0.7
' program.json > program.tmp && mv program.tmp program.json
```

What the knobs mean:
- **Three confidence thresholds** — `model.detection_threshold` (model reports a detection),
  `detections.threshold` (secondary filter), `saving_managers.saving_threshold` (file clip
  as `birds/` vs `no_birds/`). Set all to **0.7** for "act only on confident hits."
- **`saving_filters` are TIME windows, not detection-based** — `starttime`/`endtime`,
  `before/after_dawndusk_duration`, and `frequency_*` (periodic sampling, 0 = off). Widen
  `starttime`/`endtime` to the active day so detection-saving works whenever it's recording.

> During bring-up we temporarily set the thresholds to **0.3** to make a detection fire
> while testing — restore to **0.7** for real use (the LoRa sidecar also independently
> gates at 0.7).
>
> 🔒 `program.json` contains the **MQTT broker password in plaintext** — keep it out of git.

---

## Phase 7 — REQUIRED source patches (acoupi on the Uno Q)

acoupi 0.5.1 + acoupi-birdnet 0.1.1 do **not** run out-of-the-box on this hardware. Two
crashes block the pipeline; both need a one-line patch to the installed package. **Symptom
of both: clips record into `/run/shm` but pile up (hundreds) and nothing is detected/saved**
— because the task crashes *after* recording.

### 7a. Raspberry Pi serial number
```
RuntimeError: Could not find serial number of Raspberry Pi
  acoupi/tasks/recording.py -> add_guano_metadata -> acoupi/devices/rpi.py:get_rpi_serial_number
```
The recording task stamps GUANO metadata using the **Pi** serial — there's none on the Uno Q.
```bash
F=~/bioacoustics/.venv/lib/python3.11/site-packages/acoupi/devices/rpi.py
cp "$F" "$F.bak"
sed -i 's/raise RuntimeError("Could not find serial number of Raspberry Pi")/return "unoq-0000000000000000"/' "$F"
```

### 7b. `Detection.prediction_type` required (version drift)
```
pydantic ValidationError: prediction_type  Field required
  acoupi_birdnet/model.py:64  data.Detection(...)
```
acoupi 0.5.1's `Detection` requires `prediction_type` (enum `PredictionType` =
`PRESENCE|SEQUENCE|EVENT`); acoupi-birdnet 0.1.1 doesn't pass it. acoupi-birdnet builds a
`Detection` **with a bounding box**, so `EVENT` is the right value.
```bash
M=~/bioacoustics/.venv/lib/python3.11/site-packages/acoupi_birdnet/model.py
cp "$M" "$M.bak"
sed -i 's/^\(\s*\)detection_score=predicted_tag\.score,/\1prediction_type=data.PredictionType.EVENT,\n\1detection_score=predicted_tag.score,/' "$M"
grep -n 'prediction_type' "$M"   # verify the new line is present
```

> ⚠️ **These patches live in the venv and revert on any `pip install --upgrade`.** For the
> production build, pin compatible versions or report upstream rather than carrying patches.
> Same hardware → the production MQTT build needs these too (they're not LoRa-related).

---

## Phase 8 — the LoRa sidecar

acoupi has **no LoRa messenger** (only HTTP/MQTT), so we point its **HTTP messenger** at a
tiny local sidecar that re-emits detections over the LA66.

- Full script: [`lora-dragino/acoupi_lora_bridge.py`](../lora-dragino/acoupi_lora_bridge.py)
  (stdlib only — runs on system `python3`, no venv).
- It listens on `127.0.0.1:8000`, parses acoupi's message JSON, and on any detection
  **≥0.7** sends `AT+SENDB=0,2,<len>,<hex>` to the LA66 bridge at `127.0.0.1:7500`
  (duty-cycle-guarded to one uplink / 30 s).

**acoupi's real message schema** (discovered by logging the POST body — drives the parser):
```json
{ "name_model": "BirdNET",
  "recording": { "...": "..." },
  "detections": [
    { "detection_score": 0.93,
      "prediction_type": "event",
      "tags": [ { "tag": { "key": "Scientific Taxon Name",
                           "value": "Columba palumbus" },
                  "confidence_score": 0.93 } ] } ] }
```
→ confidence = `detections[].detection_score`; species = `detections[].tags[].tag.value`.

**LoRa payload** (matches `lora-dragino/ttn-payload-decoder.md`): 4-byte BE epoch, then per
detection 3 bytes = uint16 species-id + uint8 confidence%.

### Install as a systemd **user** service (survives logout/reboot, auto-restarts)
Put the script at `/home/arduino/acoupi_lora_bridge.py`, then:
```bash
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/acoupi-lora-bridge.service <<'EOF'
[Unit]
Description=acoupi to LoRa bridge sidecar
After=network.target

[Service]
ExecStart=/usr/bin/python3 /home/arduino/acoupi_lora_bridge.py
Restart=always
RestartSec=2

[Install]
WantedBy=default.target
EOF

loginctl enable-linger arduino           # if not already done
systemctl --user daemon-reload
systemctl --user enable --now acoupi-lora-bridge.service
systemctl --user status acoupi-lora-bridge.service --no-pager | head
```

> **Gotchas:**
> - A bare `http.server` handler returns **501** for any HTTP method it doesn't implement;
>   acoupi's health check probes with `HEAD`/`GET`, so the handler must answer **all**
>   methods (the script aliases them all to one handler).
> - Don't run the sidecar by hand / `pkill` it — let the service own it (manual `pkill`
>   loops were killing our own process).

---

## Phase 9 — launch & operate

```bash
# always call acoupi by full path OR `source ~/bioacoustics/.venv/bin/activate` first
/home/arduino/bioacoustics/.venv/bin/acoupi deployment stop      # if running
rm -f /run/shm/*.wav                                             # clear stale backlog
/home/arduino/bioacoustics/.venv/bin/acoupi deployment start     # re-prompts name/lat/lon
/home/arduino/bioacoustics/.venv/bin/acoupi deployment status
```

> **Gotchas:**
> - `acoupi` lives in the venv — a fresh SSH window gives `command not found`. Use the full
>   path (above) or activate the venv. The systemd service uses full paths so it's immune.
> - The flood of `ALSA … Unknown PCM` / `jack server is not running` lines is **harmless**
>   PortAudio noise. The line that matters is **`Health checks passed.`**
> - `deployment start` re-prompts for deployment name + lat/lon each time.
> - Messages flush every **`message_send_interval`** (120 s) — a detection's LoRa uplink can
>   lag up to 2 min.

Monitoring is by reading log files (no need to keep a window tailing):
- `~/.acoupi/log/recording.log` · `detection.log` · `default.log` · `beat.log`
- `~/acoupi_lora_bridge.log` (sidecar: POST bodies + `LoRa TX`)

---

## Phase 10 — verify end to end

Play a clear bird call near the AudioMoth (or rely on ambient birds), wait ~2 min, then:
```bash
tail -n 20 ~/.acoupi/log/detection.log                          # "...Storing message", no traceback
find ~/bioacoustics/data/recordings -type f                     # clips in birds/
tail -n 20 ~/acoupi_lora_bridge.log                             # POST body + "LoRa TX (... ): AT+SENDB=..."
```
Then check **LORIOT** live data for an uplink on **port 2** from the device's DevAddr.

Decode example — a real Wood Pigeon hit `6a 3f b2 e4 38 e4 5c`:
| Bytes | Meaning |
|---|---|
| `6a 3f b2 e4` | epoch (2026-06-27 11:24 UTC) |
| `38 e4` | species id (hash of `Columba palumbus`) |
| `5c` | 92 % |

---

## Phase 11 — disable the rogue test cron

An earlier crontab line ran `la66_ttn-test.py` every minute, spraying **hardcoded sample
data** (`id 1001/94%`, `1002/78%`, frozen 2026-06-25 timestamp) to LORIOT and contending on
the LA66. Remove it:
```bash
crontab -l | grep -v 'la66_ttn-test' | crontab -
crontab -l        # confirm it's gone
```

---

## Consolidated gotchas (Part 2)
1. **Two required source patches** — RPi serial (`devices/rpi.py`) and `prediction_type`
   (`acoupi_birdnet/model.py`). Without them, clips record but nothing is detected/saved.
2. Patches revert on `pip upgrade` — pin versions for production.
3. `acoupi` is venv-only → use full path in any fresh shell / systemd.
4. ALSA/jack spam is noise; `Health checks passed.` is the signal.
5. Samplerate is 192000 (PortAudio native); acoupi resamples to 48 kHz.
6. Setup `[Y/n]` prompts reject typed values — answer `n`, then supply the value.
7. Sidecar must answer **all** HTTP methods (acoupi health check) — else `501`.
8. Run the sidecar as a systemd **user** service; don't hand-run/`pkill` it.
9. Messages flush every 120 s — expect lag.
10. Kill the `la66_ttn-test.py` cron or it pollutes LORIOT with fake frames.

## Remaining polish / TODO
- **LORIOT payload decoder** — port `lora-dragino/ttn-payload-decoder.md` so the dashboard
  shows species + confidence instead of hex.
- **Real species → 2-byte ID table** (replace the hash) shared by sidecar + decoder.
- **Restore thresholds to 0.7** in `program.json` (we used 0.3 during bring-up).
- **Production switch:** repoint acoupi's messenger from HTTP/sidecar to native **MQTT**
  (`mqtt.cetools.org`) and disable the sidecar service — see `acoupi-birdnet-unoq.md` §B/§10.
- BirdNET also emits non-bird labels (`Engine`, `Human vocal`) — filter if undesired.
