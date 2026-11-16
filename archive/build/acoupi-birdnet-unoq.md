# acoupi + BirdNET on Arduino Uno Q — Setup & Messaging Plan

Sibling of [`UNOq.md`](UNOq.md) (which is the **BatDetect2** blueprint). This one is for
**BirdNET** on the Uno Q, and records the steps/gotchas we actually hit, plus the plan for
a **disable-able LoRaWAN** output that rolls back to a **static WiFi → MQTT** deployment.

---

# Part A — Install runbook (up to `acoupi setup`)

## A0. Prerequisites / hardware state
- Uno Q, user `arduino`, reachable over SSH (we used WiFi; ADB-over-USB is the fallback).
- **AudioMoth USB Microphone** plugged into the dongle → enumerates as **`card 1` "Microphone"** → ALSA `hw:1,0` / use **`plughw:1,0`** for rate conversion. (Requires the USB-C port in **host** mode — achieved by powering the board *through* the dongle.)
- **SD card** in the dongle → **`/dev/sdb`** (58 G). `/dev/sda` is the dongle's *empty* second slot.

### Deviations from `UNOq.md` (which targets the RPi/BatDetect2 build)
| `UNOq.md` says | This board / BirdNET needs |
|---|---|
| Program `acoupi_batdetect2.program` | **`acoupi_birdnet.program`** |
| Mic `hw:0,0` | **AudioMoth `hw:1` / `plughw:1,0`** |
| SD card `/dev/sda1` | **`/dev/sdb`** |
| `torch` + `torchvision` + `onnxruntime` | **none — BirdNET uses TensorFlow Lite** (skip entirely) |
| Messaging via MQTT | LoRa for testing → MQTT for production (Part B) |

## A1. System dependencies (apt)
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y log2ram python3-pip python3.13-venv libsndfile1 build-essential portaudio19-dev
sudo apt install -y python3-numpy python3-scipy gfortran libopenblas-dev liblapack-dev pkg-config rabbitmq-server jq
sudo systemctl enable --now rabbitmq-server
```
> **Gotcha:** `apt upgrade` pops the **`needrestart`** "which services to restart" dialog — Tab to `<Ok>`, Enter (defaults are fine; it won't drop your SSH). To silence it on future runs:
> `echo '$nrconf{restart} = "a";' | sudo tee /etc/needrestart/conf.d/90-auto.conf`

## A2. SD card for data storage
> **⚠️ Destructive + the card may not be blank.** Ours held a **bootable Raspberry Pi OS image** (`bootfs`/`rootfs`). Formatting erases it — confirm it's a spare. And it's **`/dev/sdb`**, not `/dev/sda1`.
```bash
lsblk -o NAME,SIZE,TYPE,LABEL,MOUNTPOINT /dev/sda /dev/sdb     # confirm sdb is the 58G card
sudo umount /mnt/sd 2>/dev/null; sudo umount /dev/sdb1 /dev/sdb2 2>/dev/null
sudo wipefs -a /dev/sdb
sudo mkfs.ext4 -L acoupidata /dev/sdb                          # WIPES the card

UUID=$(sudo blkid -s UUID -o value /dev/sdb)
sudo mkdir -p /home/arduino/bioacoustics/data
echo "UUID=$UUID /home/arduino/bioacoustics/data ext4 defaults,noatime 0 2" | sudo tee -a /etc/fstab
sudo mount -a
sudo chown -R arduino:arduino /home/arduino/bioacoustics
df -h /home/arduino/bioacoustics/data                         # expect ~57G mounted
```

## A3. Python 3.11 environment (uv)
BirdNET stack requires **Python ≥3.9,<3.12** → use **3.11** (not Debian Trixie's 3.13).
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.local/bin/env

cd ~/bioacoustics
uv venv --python 3.11 --system-site-packages     # we used this; see numpy note
source .venv/bin/activate
python --version                                  # 3.11.x
```
> **numpy gotcha:** `audioclass[birdnet]` pins **`numpy<2`**, but the system numpy is 2.x. With `--system-site-packages`, the venv installs `numpy<2` which **shadows** the system one — verify after install that `numpy.__version__` is **1.x** (we got `1.26.4`). If it ever reports 2.x, force it: `uv pip install "numpy<2"`. (An isolated venv — no `--system-site-packages` — also works and avoids the ambiguity.)

## A4. Install acoupi + BirdNET  (NOT the blueprint's torch/onnx section)
```bash
uv pip install acoupi acoupi-birdnet
uv pip install pytz            # <-- MISSING DEP gotcha (see below)
```
Dependency chain: `acoupi-birdnet → acoupi + audioclass[birdnet] → tflite-runtime`. **No PyTorch/ONNX.**

> **Gotcha — missing `pytz`:** `acoupi_birdnet/program.py` does `import pytz` but the package doesn't declare it, so `import acoupi_birdnet` fails with `ModuleNotFoundError: No module named 'pytz'` until you `uv pip install pytz`.

Verify:
```bash
python -c "import acoupi_birdnet; print('birdnet import OK')"
python -c "import tflite_runtime.interpreter as t; print('tflite OK')"
python -c "import numpy; print('numpy', numpy.__version__)"     # expect 1.x
```

## A5. acoupi setup  ← this is the point Part A ends at
RabbitMQ must be running (A1). Then:
```bash
acoupi setup --program acoupi_birdnet.program
```
Recommended answers (lock exact values with `jq` afterwards):
| Prompt | Value |
|---|---|
| Microphone | `192kHz AudioMoth USB Microphone` (card 1) |
| Samplerate | `48000` (if `hw:` rejects it, set device to `plughw:1,0` so ALSA resamples) |
| Channels | `1` |
| tmp_audio | `/run/shm` |
| recordings | `/home/arduino/bioacoustics/data/recordings` |
| metadata_db | `/home/arduino/bioacoustics/data/metadata.db` |
| messages_db | `/home/arduino/bioacoustics/data/messages.db` |
| schedule interval | `30` (s) |
| messaging | configured in Part B |

### Part A consolidated gotchas
1. Skip torch/onnx — BirdNET is **TFLite**.
2. `pytz` is an undeclared dependency — install it manually.
3. Python must be **3.11** (`<3.12`).
4. **numpy<2** vs system numpy 2.x — verify it's shadowed to 1.x.
5. SD is **`/dev/sdb`**; it may hold a real OS — formatting wipes it.
6. `needrestart` prompt during `apt upgrade`.
7. AudioMoth = `card 1` / use `plughw:1,0`.
8. RabbitMQ must be enabled for acoupi to run.

---

# Part B — Messaging plan: disable-able LoRa now, MQTT/WiFi production later

**Design principle:** all routing lives in **one place** — the `.messaging` block of
`~/.acoupi/config/program.json`. Swapping output = swap that block (+ start/stop one
helper service). acoupi itself, the model, recording, and **detection-only saving** never
change between modes.

Common to both modes (configure once, via `jq` after `acoupi setup`):
- **Save only on detection ≥ 0.7** — acoupi `saving_filters` with a `saving_threshold`.
- **Emit a message only on detection ≥ 0.7** — `DetectionThresholdMessageBuilder`.
- (Exact field paths: confirm against the generated `program.json` — acoupi exposes
  `.messaging.*`, `.saving_filters.*`; the `UNOq.md` examples show `.messaging.mqtt.port`, etc.)

## Mode 1 — TEST: LoRa (disable-able overlay)
acoupi's built-in **HTTP messenger** → a tiny **local sidecar** → LA66 → LORIOT.

1. Point acoupi at a local URL (HTTPConfig): `.messaging` → HTTP, `url=http://127.0.0.1:8000`.
2. **Sidecar** `~/acoupi-lora-bridge.py`: HTTP listener on :8000 that, per detection message,
   encodes a compact payload and sends it via the **proven LA66 path** (`AT+SENDB=0,2,…`
   to `127.0.0.1:7500`). Reuse the encoder in `lora-dragino/la66_ttn-test.py`
   (4-byte epoch + 3-byte `species_id|confidence` blocks; LORIOT decoder = port of
   `lora-dragino/ttn-payload-decoder.md`).
3. Run sidecar as `acoupi-lora-bridge.service` (systemd, `loginctl enable-linger arduino`).
4. *(Nice-to-have, skip if it slows us down)* sidecar also renders a spectrogram PNG from
   the saved WAV — acoupi does **not** do spectrograms natively.

> **LoRa payload design note:** BirdNET has thousands of species but a LoRa frame is tiny —
> the `SPECIES_LUT` in `la66_ttn-test.py` is a 2-species stub. For real use, map BirdNET
> labels → compact 2-byte IDs (e.g. BirdNET label-list index) on both the device and the
> LORIOT decoder. (No such limit on MQTT — full JSON is fine there.)

## Mode 2 — PRODUCTION: static WiFi → MQTT (the rollback target)
acoupi's **native MQTT messenger** — no custom code, no sidecar.

1. `.messaging` → MQTT (`MQTTConfig`): `host`/`port`/`topic`/`timeout`/credentials of your broker.
2. WiFi already configured via NetworkManager (board auto-joins saved profiles).
3. Disable the LoRa sidecar service. acoupi publishes detections straight to the broker.

## The toggle (roll LoRa back cleanly)
Keep two snippets and flip between them:
```bash
acoupi deployment stop
# swap the .messaging block (HTTP/sidecar  <->  MQTT/broker) via jq, e.g.:
#   jq '.messaging = input' program.json messaging-mqtt.json > tmp && mv tmp program.json
sudo systemctl disable --now acoupi-lora-bridge.service     # LoRa off  (or enable for LoRa on)
acoupi deployment start
```
Rollback checklist: ① messenger block = MQTT ② sidecar service disabled ③ WiFi/broker
reachable ④ `acoupi check` passes ⑤ confirm a detection lands on the MQTT broker.

---

## Bench test before any walk
Play a known bird call near the AudioMoth and confirm, in order:
1. a WAV appears under `…/data/recordings/` (detection-only),
2. acoupi logs a detection ≥0.7 (`~/.acoupi/log/`),
3. **Mode 1:** a LORIOT uplink arrives / **Mode 2:** a message lands on the MQTT broker.
