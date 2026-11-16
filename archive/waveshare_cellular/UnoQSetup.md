# Arduino Uno Q — Acoupi/BatDetect2 Build Runbook

Reproducible, from-scratch build of the Acoupi + BatDetect2 bat-detection pipeline on the
Arduino Uno Q (Qualcomm Dragonwing Linux side). Follow Parts 1–10 in order after flashing.

This runbook is the **canonical sequence** — it uses the *corrected* commands we proved work on
the hardware. Where it departs from the original team blueprint, the reason is in **Appendix B**.
Verified facts about the board are in **Appendix A**; problems we hit and how we solved them are in
**Appendix C**.

Legend for commands: 🖥️ = on the board (App Lab terminal or SSH) · 💻 = on the laptop.

**Verification status (be honest during the rebuild):**
- **Parts 1–9 + `acoupi check` — hardware-verified on `dave` (2026-07-01).** Install, venv, packages
  (import-checked), acoupi setup/config, MQTT round-trip to the live broker, AudioMoth hosted via a PD-hub
  in USB host mode, 192 kHz capture proven (both `arecord` and `acoupi check` green — through the ALSA
  noise; `acoupi check` prints "Health checks passed." exit 0).
- **Part 10 live deployment — NOT yet proven.** `acoupi deployment start` and the full loop
  (record → BatDetect2 inference → detection message on MQTT) haven't been run end-to-end. Next to prove:
  a real detection reaching the broker, and RAM-disk (`/run/shm`) churn staying healthy under load.

---

## 0. Read first — key facts & gotchas

- **Board:** Debian 13 (trixie), aarch64, kernel 7.0.0, user `arduino` (uid 1000, has sudo),
  hostname `dave`, system Python 3.13. 4 cores / 3.6 GB RAM.
- **Two ways in:** ADB-over-USB (the channel App Lab uses) and SSH-over-Wi-Fi. The single USB-C is
  either the laptop link **or** a USB host for a mic — not both (see Part 9).
- **Wi-Fi must be NON-isolated.** Public/guest APs (e.g. "thecloud") isolate clients, so the laptop
  can't reach the board. Use a home router or a hotspot that allows device-to-device.
- **Address the board as `arduino@dave.local`** (mDNS). Its DHCP IP changes across reboots; the
  hostname does not. A stale IP gives "Connection refused" (another device now holds that lease).
- **Gotchas that will bite you:**
  - `adb shell` sets `TMPDIR=/data/local/tmp` (absent here) → `export TMPDIR=/tmp` before any `uv`
    command run over ADB. Not needed in an App Lab terminal or SSH session.
  - PowerShell expands `$var`/`$(...)` inside double quotes *before* sending to adb/ssh — single-quote
    remote commands or push a script file.
  - `acoupi config set` cannot accept a JSON **object** (only scalars it can coerce). For object
    fields like `messaging.mqtt`, edit `program.json` directly then validate via `acoupi config get`.

---

## 1. Flash & first boot (Arduino App Lab)

1. Flash the Uno Q image via Arduino App Lab.
2. In App Lab onboarding set: **a login password** for `arduino`, and **Wi-Fi** (pick a non-isolated
   network — see Part 2). This is the easiest place to set Wi-Fi credentials initially.
3. Confirm you can open an **App Lab terminal** (it logs in as `arduino`) and that `sudo` works with
   your password — that alone proves the OS and account are healthy.

---

## 2. Network & remote access

### 2a. Wi-Fi credentials (🖥️ — if not set in App Lab, or to change network)
```bash
sudo nmcli device wifi rescan
nmcli device wifi list                       # find SSID; CHAN 1-13 = 2.4GHz, 36+ = 5GHz
sudo nmcli device wifi connect "YOUR_SSID" password "YOUR_WIFI_PASSWORD"   # add: hidden yes  (if hidden)
hostname -I                                  # note the IP (ignore 172.17.0.1 = docker bridge)
```
- Pick a **non-guest** network or the laptop won't be able to reach the board.
- If `nmcli` says "No network with SSID found", the board's radio may not see that band — rescan,
  and prefer a 2.4 GHz SSID if the board is 2.4-only.

### 2b. SSH access (💻 then 🖥️)
```bash
# 💻 confirm sshd is reachable (use the hostname, not a hardcoded IP):
ssh arduino@dave.local            # password login should work
```
Recommended — passwordless **key auth** (needed for headless/field use):
```bash
# 💻 (PowerShell) generate a key if you don't have one — press Enter twice for an empty passphrase:
ssh-keygen -t ed25519 -f $env:USERPROFILE\.ssh\id_ed25519
#   NB: `-N ""` for an empty passphrase is unreliable in PowerShell; either press Enter twice above,
#   or force it via cmd:  cmd /c 'ssh-keygen -t ed25519 -f "%USERPROFILE%\.ssh\id_ed25519" -N ""'
Get-Content $env:USERPROFILE\.ssh\id_ed25519.pub     # copy this whole line

# 🖥️ add it on the board:
mkdir -p ~/.ssh && chmod 700 ~/.ssh
echo "PASTE_PUBLIC_KEY_LINE" >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys
```
After this, `ssh arduino@dave.local` is passwordless.

### 2c. (Fallback) ADB over USB (💻)
```bash
adb devices -l                    # board shows as serial 1603135366
adb shell '<command>'             # run one command;  adb pull/push for files
```

---

## 3. System dependencies (🖥️ sudo)

```bash
sudo apt update
sudo apt upgrade -y                # may pull a new kernel and reboot — reconnect via dave.local
sudo apt install -y log2ram python3-pip python3.13-venv libsndfile1 build-essential portaudio19-dev
sudo apt install -y python3-numpy python3-scipy gfortran libopenblas-dev liblapack-dev pkg-config rabbitmq-server jq
sudo systemctl enable --now rabbitmq-server
systemctl is-active rabbitmq-server   # expect: active
```
Note: apt `python3-numpy/scipy` are built for system Python 3.13 and are **not** used by the 3.11
venv below (they come from PyPI wheels instead). Harmless; kept for parity with the blueprint.
(`log2ram` spares the eMMC; `rabbitmq-server` is the Celery broker; `jq` edits configs.)

---

## 4. Python environment — uv + Python 3.11 venv (🖥️)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh        # if over ADB: prefix `export TMPDIR=/tmp;`
source $HOME/.local/bin/env
mkdir -p ~/bioacoustics && cd ~/bioacoustics
uv venv --python 3.11 --system-site-packages           # uv downloads CPython 3.11
source ~/bioacoustics/.venv/bin/activate
```

---

## 5. Python packages (🖥️, venv active) — use these exact, matched versions

```bash
# Matched CPU-only torch trio FROM THE CPU INDEX (torchaudio MUST be on this line — see Appendix B):
uv pip install --index-url https://download.pytorch.org/whl/cpu \
    torch==2.11.0 torchvision==0.26.0 torchaudio==2.11.0

# ONNX Runtime that supports NumPy 2.x (NOT the blueprint's 1.16.3):
uv pip install onnxruntime==1.27.0

# acoupi + BatDetect2 plugin + pytz (pytz is needed but not pulled in automatically):
uv pip install acoupi acoupi-batdetect2 pytz
```
Verify everything imports (use the helper `test_imports.py` in the project folder, or a quick check):
```bash
python -c "import torch,torchaudio,torchvision,onnxruntime,numpy,scipy,librosa,numba,batdetect2,acoupi,acoupi_batdetect2,celery; import paho.mqtt.client; print('imports OK', torch.__version__)"
```
Expected: all import; `torch 2.11.0+cpu`, `cuda=False`. (onnxruntime prints a harmless "no GPU" note.)
If `torchaudio` FAILS to import here, the `acoupi-batdetect2` install re-resolved it off the pinned
trio — reinstall the trio from the CPU index (the line above) and re-run this check. This import
check is the gate: it must pass before Part 7.

---

## 6. Storage

Default (this build): use the existing `/home/arduino` ext4 partition (~18 GB).
```bash
mkdir -p ~/bioacoustics/data
```
Optional external SD/USB card: **identify it by size with `lsblk` FIRST**, never blind-`mkfs /dev/sda1`
(that device name is a guess and can wipe the wrong disk). Then `mkfs.ext4`, capture UUID, add an
`fstab` line mounting it at `~/bioacoustics/data`, `mkdir -p` the mountpoint, `sudo mount -a`.

---

## 7. acoupi setup & configuration (🖥️, venv active)

```bash
acoupi setup --program acoupi_batdetect2.program --no-prompt   # creates ~/.acoupi/config/{program,celery}.json
```
> **Expect a wall of ALSA/JACK warnings** during setup's "Setting up microphone configuration" step —
> `Unknown PCM cards.pcm.rear/hdmi/modem…`, `unable to open slave`, `jack server is not running`,
> `JackShmReadWritePtr … Init not done`. This is PortAudio enumerating every default PCM and finding no
> hardware behind them (only the onboard codec, no mic, no JACK daemon). **Harmless — setup runs to
> completion and returns your shell.** The same noise reappears in `acoupi check` (Part 10).
>
> **`acoupi setup` writes a *placeholder* mic config — it does NOT detect hardware.** With no mic
> attached it still sets `microphone.device_name = "192kHz AudioMoth USB Microphone"`,
> `samplerate = 48000`, `audio_channels = 1` (note the 48 kHz contradicts the "192kHz" in the name —
> both are just template defaults). You **must** overwrite these in Part 9 with the real `arecord -l`
> name and the mic's true samplerate, or capture will fail / run at the wrong rate. Check what it wrote:
> `jq '.microphone' ~/.acoupi/config/program.json`

Apply path + interval overrides (use `--field`; these are scalars so the CLI handles them):
```bash
acoupi config set --field paths.recordings /home/arduino/bioacoustics/data/recordings
acoupi config set --field paths.db_metadata /home/arduino/bioacoustics/data/metadata.db
acoupi config set --field messaging.messages_db /home/arduino/bioacoustics/data/messages.db
acoupi config set --field recording.interval 30        # BatDetect2 takes 8-27s; 10s default too tight
```
Celery — force serial execution (edit the separate `celery.json`):
```bash
jq '.worker_concurrency=1 | .task_acks_late=false' ~/.acoupi/config/celery.json > /tmp/c.json && mv /tmp/c.json ~/.acoupi/config/celery.json
```
MQTT — **must edit program.json directly** (`acoupi config set` can't take a JSON object). Schema:
`MQTTConfig{host(req), username(req), password, topic, port=1884, timeout=5, use_tls=false}`.
```bash
read -s -p "MQTT password: " MQTT_PW; echo     # type password at the hidden prompt (NOT on this line)
jq --arg pw "$MQTT_PW" \
  '.messaging.mqtt = {host:"mqtt.cetools.org",port:1884,username:"CEDevice",password:$pw,topic:"yeo/unoq-bat/acoupi",timeout:15,use_tls:false}' \
  ~/.acoupi/config/program.json > /tmp/p.json && mv /tmp/p.json ~/.acoupi/config/program.json
unset MQTT_PW
acoupi config get --field messaging.mqtt        # validates on read; ⚠ prints the password in PLAINTEXT — redact before sharing/pasting
```
Confirm the rest with `acoupi config get`.

---

## 8. Verify MQTT end-to-end (🖥️ — no mic required)

Use `mqtt_test.py` (reads creds from program.json; publishes to the topic and subscribes to confirm
round-trip). Expect: TCP OK → CONNACK Success → publish → received back.
```bash
scp mqtt_test.py arduino@dave.local:/tmp/   # 💻 (or already present)
ssh arduino@dave.local '~/bioacoustics/.venv/bin/python /tmp/mqtt_test.py'
```
Note: MQTT topics are ephemeral — a viewer only shows the topic when a message flows while subscribed,
or if published **retained**. (`mqtt_pub.py` sends a live burst + one retained message for viewer checks.)

---

## 9. Microphone + field/independent mode

The Uno Q's USB-C is in **device mode** while tethered to the laptop, so it can't host a mic. To run
standalone you flip it to **host mode** via a powered hub and reach the board over Wi-Fi.

**Order matters — establish Wi-Fi access BEFORE removing USB, or you lock yourself out.**
1. Board on a non-isolated network (Part 2a); confirm `ssh arduino@dave.local` works.
2. **USB-C PD hub** → board's USB-C, powered from the hub's **PD input** (NOT the VIN pins — host
   mode doesn't work on VIN). This powers the board and switches the port to host.
3. Unplug the direct laptop→board USB cable (you're now reaching it over Wi-Fi).
4. **AudioMoth (USB-Microphone firmware) → hub USB-A port.** Verify it enumerates, then prove capture:
   ```bash
   cat /proc/asound/cards    # NEW card for the AudioMoth. ⚠ it may grab card 0 and bump the onboard
   arecord -l                #   codec to card 1 — USB enum order isn't stable; do NOT rely on the index.
   # prove the mic records at ultrasonic rate — address it BY NAME, not index:
   arecord -D hw:CARD=Microphone,DEV=0 -f S16_LE -r 192000 -c 1 -d 3 /tmp/bat_test.wav && echo OK
   ```
   Verified on `dave`: enumerates as `192kHz AudioMoth USB Microphone` (openacousticdevices.info), grabs
   **card 0** (onboard codec → card 1), captures a clean **192 kHz / mono / 16-bit** WAV (exit 0, 576000
   frames for 3 s).
5. Point acoupi at the mic. **acoupi 0.5.2 records via PyAudio** (not sounddevice). PyAudio reports the
   device as `192kHz AudioMoth USB Microphone: Audio (hw:0,0)`, but acoupi's `parse_device_name()`
   **strips the `: … (hw:x,y)` suffix** before matching — so set `device_name` to the **bare** name
   (exactly what `arecord -l` shows), NOT the full PyAudio string, or the match silently fails.
   Use the mic's native max rate:
   ```bash
   acoupi config set --field microphone.device_name "192kHz AudioMoth USB Microphone"
   acoupi config set --field microphone.samplerate 192000     # AudioMoth USB-Mic native max (PyAudio default_sr)
   acoupi config get --field microphone                       # confirm: samplerate 192000, channels 1
   ```
   ⚠ `acoupi setup` writes a placeholder mic whose `device_name` is coincidentally this exact string but
   whose **`samplerate` is 48000** — capturing at 48 kHz throws away everything above ~24 kHz (i.e. most
   bat calls, 20–120 kHz). Always overwrite `samplerate` to the real ultrasonic rate. A normal/headset
   mic (≤48 kHz) only validates the pipeline; real bats need the ultrasonic mic.

---

## 10. Launch & make it autonomous (🖥️)

```bash
sudo loginctl enable-linger arduino     # lets the pipeline survive logout/disconnect + reboot
# optional clean-start: rm -f /run/shm/*.wav ~/.acoupi/celerybeat-schedule.db
acoupi check                            # health checks (mic+storage+broker+model). VERIFIED on dave: prints
                                        # "Health checks passed." exit 0 — through the ALSA noise (harmless)
acoupi deployment start --name "uno-q-bat-01" --latitude <lat> --longitude <lon>
acoupi deployment status
```
**Device identity / fleets:** the hardware id (`get_device_id()` = MAC-derived, here `109557275454149`)
is only the MQTT *client_id* and is NOT visible to subscribers. Distinguish devices by the
**deployment** embedded in every message payload (`recording.deployment` = id, **name**, lat, lon).
So give each board a unique `--name`. All devices share topic `yeo/unoq-bat/acoupi` unless you set a
unique `topic` per device.

---

## 11. Operations

- **View the broker:** no built-in web UI. Use **MQTT Explorer** (desktop): host `mqtt.cetools.org`,
  port `1884`, `mqtt://` (no TLS), user `CEDevice` + password, subscribe `yeo/unoq-bat/acoupi` or `#`.
  CASA/CE may also have an InfluxDB→Grafana dashboard — ask the team.
- **Retrieve recordings:** `scp -r arduino@dave.local:~/bioacoustics/data/recordings/bats/ <local>`
  (or `adb pull ...` if on USB).
- **Logs:** `tail -f ~/.acoupi/log/recording.log` (audio loop), `~/.acoupi/log/default.log` (AI step).
- **RAM-disk health:** `watch -n1 "ls -lh /run/shm"` — files should be processed and removed, not pile up.
- **Safe config edits:** `acoupi deployment stop` → edit → `acoupi deployment start`.

---

# Appendix A — Verified board environment

| Fact | Value |
|---|---|
| OS | Debian GNU/Linux 13 (trixie) |
| Arch | aarch64 (ARM64) |
| Kernel | 7.0.0 (May 2026; >Nov-2025 so includes the USB-host fix) |
| System Python | 3.13.5 |
| User | `arduino` (uid 1000); groups incl. sudo, audio, dialout, video, docker, gpiod |
| Hostname | `dave` (→ `dave.local` via mDNS) |
| CPU / RAM | 4 cores / 3.6 GiB + 1.8 GiB zram swap |
| Storage | eMMC `mmcblk0` 29 GB: p67 vfat /boot/efi, p68 10 GB ext4 /, **p69 18.2 GB ext4 /home/arduino** |
| MQTT broker | mqtt.cetools.org:1884, user `CEDevice`, no TLS, topic `yeo/unoq-bat/acoupi` |
| Final venv versions | torch/torchaudio/torchvision 2.11.0+cpu/2.11.0+cpu/0.26.0+cpu, onnxruntime 1.27.0, numpy 2.4.x, scipy 1.17.x, batdetect2 1.3.1, acoupi-batdetect2 0.3.0, celery 5.6.3 · **NB** the `dave` rebuild (2026-07-01) resolved **acoupi core 0.5.2** — newer than when this table was written; other pins may have advanced too, so re-capture `uv pip list` after any rebuild. acoupi's mic backend is **PyAudio** (needs `portaudio19-dev`, installed in Part 3). |

# Appendix B — Deviations from the original blueprint (with rationale)

1. **Connection:** blueprint assumed SSH-over-Wi-Fi from the start; on isolated public APs that fails.
   We used ADB-over-USB initially, then SSH (key auth) once on a non-isolated network. Use `dave.local`.
2. **torch trio pinned & torchaudio on the CPU-index line.** Blueprint left torch unpinned and omitted
   torchaudio; pip then resolved torch 2.12.1 (cpu) but torchaudio from PyPI as a **CUDA** build that
   won't load on this CPU board, and the two were ABI-mismatched. Fix: install
   `torch/torchvision/torchaudio == 2.11.0/0.26.0/2.11.0` together from `.../whl/cpu`.
3. **onnxruntime 1.16.3 → 1.27.0.** 1.16.3 is built against NumPy 1.x and crashes under the NumPy 2.x
   that torch/numba/pandas/scipy require (`_ARRAY_API not found`).
4. **Added `pytz`** — `acoupi_batdetect2` imports it but pandas 3.0 no longer pulls it in.
5. **Dropped `--no-binary batdetect2,acoupi-batdetect2`** — they're pure-Python; forcing source builds
   just slowed installs with no benefit.
6. **MQTT via direct `program.json` edit**, not `acoupi config set --field messaging.mqtt '<json>'` —
   the CLI has no `--json` flag and passes the value as a string, which fails schema validation.
   (Worth reporting upstream.)
7. **Config field names** differ from the blueprint: `paths.db_metadata` (not metadata_db),
   `messaging.messages_db` (not paths.messages_db), `recording.interval` (no `scheduler` section),
   Celery settings live in `celery.json`.
8. **SD card section skipped** — no external card attached; `/home/arduino` (18 GB ext4) is used.
   The blueprint's blind `mkfs.ext4 /dev/sda1` is the one genuinely destructive step — never run it
   without identifying the device by size via `lsblk` first.

# Appendix C — Troubleshooting log (symptoms → cause → fix)

- **SSH "connection timed out":** board on an isolated AP (different subnet, client isolation). → move
  to a non-isolated network.
- **SSH "connection refused" after reboot:** DHCP gave the board a new IP; the old IP now belongs to
  another host. → use `arduino@dave.local` (mDNS).
- **`uv`/installer `mktemp ... No such file or directory` over ADB:** `TMPDIR=/data/local/tmp` doesn't
  exist. → `export TMPDIR=/tmp` first (ADB only).
- **`adb shell` mangling commands / PowerShell eating `$`:** quote/escape issues. → single-quote remote
  commands, avoid `()<>|$` inline, or push a script file. App Lab terminal / SSH avoid this.
- **torchaudio `_torchaudio.abi3.so` won't load / CUDA libs:** torch↔torchaudio version/ABI mismatch &
  CUDA build on a CPU board. → matched CPU trio (Appendix B #2).
- **onnxruntime `_ARRAY_API not found`:** NumPy 1.x-built onnxruntime under NumPy 2.x. → onnxruntime ≥1.19.
- **`acoupi config set` ParameterError on messaging.mqtt:** CLI can't ingest JSON objects. → edit
  program.json with jq (Part 7).
- **No mic / `arecord` "audio open error":** only the onboard output codec; no capture device, and the
  USB-C can't host a mic while it's the laptop link. → PD hub host mode + USB mic (Part 9).
- **`acoupi setup` floods ALSA `Unknown PCM …` + JACK `jack server is not running` / `JackShmReadWritePtr`
  errors:** PortAudio enumerating default PCMs with no real capture device and no JACK daemon during the
  mic-config step. → Harmless; setup finishes and returns the shell. But it then writes a **placeholder**
  mic (`"192kHz AudioMoth USB Microphone"` @ 48000, 1ch) regardless of attached hardware — correct it in
  Part 9 from `arecord -l`; never trust the auto-written mic value.
- **acoupi "no input device" / mic not found even though `arecord -l` sees it:** `microphone.device_name`
  was set to the full PyAudio string (`… : Audio (hw:0,0)`). acoupi's `parse_device_name()` strips that
  suffix before comparing, so it never equals your value. → Set `device_name` to the **bare** card name
  from `arecord -l` (e.g. `192kHz AudioMoth USB Microphone`).
- **AudioMoth enumerates as card 0, onboard codec becomes card 1 (was card 0):** USB enumeration order
  isn't stable and can change again after a reboot. → Never address the mic by card index; use the name
  (`arecord -D hw:CARD=Microphone,…`) and acoupi's name-based `device_name`, which are index-independent.
- **MQTT topic not visible in viewer:** topics are ephemeral; non-retained messages vanish after
  delivery. → publish a retained message, and check viewer subscription (`#`) / account ACL.
