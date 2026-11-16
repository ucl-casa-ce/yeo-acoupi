# Arduino UNO Q — Quirks & Gotchas

Hard-won notes from bringing up an Arduino UNO Q (Qualcomm Dragonwing QRB2210 Linux MPU
+ STM32U585 MCU, "dual-brain") for the cellular project. Written after a multi-day
debugging slog so we don't repeat it. Grouped by the point in a bring-up where you'll
hit each thing.

> **The single most important lesson:** almost every "the board is dead / won't appear"
> symptom we chased for two days was **power**. See §1 first, always.

---

## Quick reference

| Thing | Value |
|---|---|
| Normal USB identity (booted) | `2341:0078` "Arduino UNO Q" → `/dev/ttyACM0` **+ ADB device** |
| EDL / download-mode identity | `05c6:9008` "Qualcomm … (QDL mode)" → `/dev/ttyUSB0` (qcserial) |
| ADB shell user | `arduino` (uid 1000); in `sudo` and `docker` groups |
| `adb root` | **Blocked** (production build) |
| Flash tool | `arduino-flasher-cli` — **must run with `sudo`** (qdl needs raw USB) |
| Debug console | **JCTL** connector, **1.8 V logic**, 115200 8N1 |
| EDL pins | The two pins on **JCTL** furthest from the USB-C connector |
| Kernel | `7.0.0-g122c2c22d838` — **no `ppp_generic`** (no PPP/`ppp0`) |
| Boot-done indicator | LED matrix: figure-8 (booting) → **heart** (ready) → blank+solid green/blue (idle) |

---

## 1. Power — the root of almost everything

### Symptom
Board appears completely dead: LED matrix stuck on the figure-8 animation and **never
reaches the heart**; never enumerates on USB; never joins WiFi. Looks like a bricked or
faulty board. We chased "hardware fault," cable faults, reflashes, etc. for two days.

### Cause
The Qualcomm SoC pulls a **hard current spike at boot**. Two traps:

1. **"Adaptive" / PD / QC chargers underpower it — even when rated 5 V/3 A.** A charger
   that *negotiates* (USB-PD, Quick Charge, "smart" laptop bricks) may sit at 5 V but
   throttle current until a handshake the board doesn't complete — so the label says 3 A
   but the board gets far less at the exact moment it needs it. **A 5 V/3 A rating is not
   enough if the supply is adaptive.**
2. **Host USB ports are marginal.** A PC USB-C port *can* deliver enough (it worked for us
   once) but isn't guaranteed; legacy USB-A (~0.5–0.9 A) will not.

### Fix
- Use a **dumb power supply that delivers full 5 V current from the first millisecond** —
  a plain 5 V/3 A brick or a good BC1.2 charger. **This was the fix. It boots every time.**
- A **powered USB hub** between PC and board gives data *and* full current together.
- Single USB-C caveat: the one port carries **both power and data**. You can't power from
  a wall brick *and* have a data link to the PC at the same time. (`VIN` is the alternate
  power input, but the UNO Q's VIN voltage spec was never confirmed — **do not guess it**;
  the UNO R4's 6–24 V does **not** apply.)
- **Field rig (powered USB-C hub):** the deployment fans the UNO Q's single USB-C out through a
  **powered hub** to modem + microSD + AudioMoth, all fed from **one 5 V/3 A brick into the hub's
  PD input**. That ~15 W is shared across the UNO Q's boot draw *and* the modem's ~2 A TX spikes —
  **tight**. It booted and published fine on the bench, but watch for brownout under combined load
  in the field; give it headroom (bigger brick / a hub that can source more) if it misbehaves.

### Tell-tale
LED matrix stuck on figure-8 = it's not finishing boot; on a good supply it goes
figure-8 → heart within seconds. If a supply gets it to the heart, power is fine.

---

## 2. Boot indicators (LED matrix + LEDs)

- **Figure-8 / infinity animation** = booting (factory boot animation, driven by the STM32).
- **Heart (a couple of pulses)** = booted / ready.
- **Blank matrix + solid green + solid blue LEDs** = booted and idle — **this is normal**,
  not a fault. (The boot animation finishes and the matrix clears.)
- **Stuck on figure-8, never a heart** = boot not completing → almost always **power** (§1).

---

## 3. Detecting the board on the host

### When booted and cabled to a PC
Enumerates as **`2341:0078` "Arduino UNO Q"** → `/dev/ttyACM0`, **and** as an **ADB device**
(`adb devices` shows it `device`/authorized). That's your channel before WiFi exists.

### Gotchas
- **Single USB-C:** if it's powered from a wall brick (not the PC), there is **no data link**
  to the PC — nothing shows on USB even though it's running. Cable it to the PC (with enough
  power) to get ADB/serial.
- **A clean image is NOT on WiFi** (no credentials) until you provision it — so find it over
  **USB/ADB first**, not the network.
- **Network-scan trap 1:** a booted board whose `sshd` is down (no host keys — see §5) shows
  up as a LAN host with **port 22 closed** — easy to overlook when scanning for SSH.
- **Network-scan trap 2:** DHCP may hand the board an IP you'd previously catalogued as a
  different device. We lost it for ages at `192.168.68.116`, which had earlier belonged to an
  Apple device — our "known hosts" filter skipped it. When hunting, scan **every** live host,
  don't exclude "known" ones.

---

## 4. Flashing & EDL (recovery)

### Enter EDL (Emergency Download / QDL) mode
1. Disconnect power.
2. Short the **two EDL pins on the JCTL header** (the pair **furthest from the USB-C**) with
   a jumper.
3. Connect USB-C to the PC.
4. Confirm: `lsusb | grep 05c6:9008` → "Qualcomm … (QDL mode)". A `/dev/ttyUSB0` appears.
5. You can remove the jumper now; it stays in EDL until power-cycled without the short.

### THE flashing gotcha: `qdl` needs root, and a failed flash looks like a success
Running the flasher as a normal user:
```
Flashing with qdl
qdl: unable to open USB device
Waiting for EDL device
```
The tool **downloads and unzips the image fine first**, so it *looks* like it worked — but
`qdl` (which does the actual write) **couldn't open the USB device, so nothing was written.**
We flashed "clean" twice and both times the board kept its old contents because of this.

**Fix: run the flasher with `sudo`** (qdl needs raw access to the `9008` device):
```bash
cd /home/mfo/arduino-flasher-cli
sudo ./arduino-flasher-cli flash latest
```
**Success looks like** `qdl` naming and writing partitions with progress — *not*
"unable to open USB device". Watch for that; don't trust "download complete".

### Clean vs. modified image — the path matters
- `flash latest` or `flash <archive>.tar.zst` → **pristine** image.
- `flash <extracted-folder>` → flashes **whatever is in that folder**, including any edits
  you made to it. A bare folder path is **not** guaranteed clean.

### Verify a flash actually took
Don't assume. After boot, check that old artifacts are **gone** (e.g. a WiFi profile or
`authorized_keys` you'd baked in) or that expected changes are **present**. If the board
"remembers" WiFi/logins after a supposed clean flash, the flash **didn't write** (see qdl above).

---

## 5. Gaining access (ADB / SSH / root)

- **ADB shell = `arduino`** (uid 1000), a member of `sudo` and `docker`.
- **`adb root` is blocked** (production build) — you don't get a root adbd.
- **`sudo` needs a password**, which is **set by App Lab during provisioning**. On a fresh,
  un-provisioned image there is **no password**, so `sudo` is unusable until App Lab runs
  (or you provision another way).
- **`docker` group ≈ root** (you can mount the host FS in a privileged container). Powerful,
  but it's a privilege-escalation pattern — use deliberately, and note tooling/sandboxes may
  block it.
- **SSH on a bare image:** `openssh-server` is installed but **not enabled and has no host
  keys**. So `ssh.service` is `failed` and nothing listens on 22 until something runs
  `ssh-keygen -A` + enables/starts it. **App Lab's setup does this** (generates host keys,
  starts sshd). Without App Lab you must do it yourself (needs root).
- **Root SSH:** default `PermitRootLogin prohibit-password` allows **key-based** root login
  (no password) if a key is in `/root/.ssh/authorized_keys`. "Logged in as root with no
  password" = your SSH key was accepted, not a missing password.
- **Get an access channel BEFORE detaching from USB.** ADB only exists while the board's USB-C is
  on the PC. Moving the board to the field hub kills ADB, and you won't have the App-Lab `arduino`
  password on the CLI — so **install your SSH pubkey on `arduino` first** while ADB is still up
  (`ssh-copy-id -i ~/.ssh/id_ed25519.pub arduino@<host>.local`, or append to
  `~/.ssh/authorized_keys` via `adb shell`), or you'll be locked out.
- **polkit split:** `nmcli` (WiFi *and* `gsm` connection add) works for the `arduino` user
  **without sudo** via polkit; ModemManager `Device.Control` (raw-AT passthrough, connect) does
  **not** — those need root/debug-mode (see §6).

---

## 6. Cellular modem & data path (Waveshare A7670E) — the whole saga

WiFi is NetworkManager (`nmcli dev wifi connect <SSID> password <PW>`, or App Lab). Cellular
is a different beast on this board. Everything we learned:

### Seeing the modem
- The A7670E connects by **USB** (through the hub) → enumerates as **`/dev/ttyUSB0/1/2`**
  (`option`/`simtech` driver); `lsusb` id `1e0e:9011`.
- **ModemManager** (on by default) recognizes it — `mmcli -L` → `SIMCOM … A7670E-LASE` — and
  claims **`ttyUSB1`(at)** / `ttyUSB2`(at); `ttyUSB0` is the ignored diag port.
- `/dev/ttyUSB*` are `root:dialout`; `arduino` is in `dialout`, so **no sudo to talk to the
  modem** — *once ModemManager releases the ports*.

### SIM / registration gotchas
- **SIM must be activated**, and **IoT/roaming SIMs can take 15–60+ min to provision** after
  activation (our Tele2 "keysim" sat at `0% / idle` for exactly this reason; a known-good
  Giffgaff SIM came up **O2-UK, LTE, 96%** instantly).
- **`signal quality: 0%` + `registration: idle`** → check in order: SIM activated → antenna on
  the **MAIN** (not GNSS) u.FL connector → coverage.
- **APN must match the SIM**: `giffgaff.com` for Giffgaff, `key` for the keysim
  (`AT+CGDCONT?` shows it; stored per PDP context).
- **Changing the APN needs a re-attach — this bit us.** If the modem attached with the wrong
  APN you get `AT+CEREG? → 0,3` (EPS/**data** registration *denied*) and `AT+CGACT=1,1` →
  `+CME ERROR`, *even with good signal and a network found* (`AT+COPS?`). Fix: set the APN
  (`AT+CGDCONT=1,"IP","<apn>"`) **then `AT+CFUN=1,1`** to force a fresh attach — it comes back
  `AT+CEREG? → 0,5` (registered/roaming), the context activates, and data flows. The keysim
  needed exactly this (roaming on Vodafone-UK, APN `key`, then `CFUN=1,1`).

### Transport: PPP is dead, ECM is unavailable, the AT stack wins
- **PPP is gone.** `pppd` absent, `ppp_generic` **stripped and not loadable**; a native
  `nmcli … type gsm` connect fails with **`PPP failed`**. Confirmed live.
- **ECM/RNDIS host interface: not achievable here.** RNDIS host isn't built
  (`# CONFIG_USB_NET_RNDIS_HOST is not set`); the ECM driver (`cdc_ether.ko`) *is* present —
  **but this A7670E firmware doesn't support `AT+CUSBPIDSWITCH`** (returns `ERROR`), so the modem
  can't be flipped into a network-interface mode. No host `usbX`/`wwan` interface is possible.
- **✅ What works: the modem's own TCP/IP stack over AT.** It attaches, gets an IP
  (`AT+CGPADDR` → e.g. `10.151.94.149`), and networks internally. Validated end-to-end:
  - **DNS:** `AT+CDNSGIP="google.com"` → resolved over cellular.
  - **MQTT:** `AT+CMQTTSTART/ACCQ/CONNECT/TOPIC/PAYLOAD/PUB` to `broker.hivemq.com` QoS 1 →
    **`+CMQTTPUB: 0,0`** (broker ACK). Telemetry out over Giffgaff/O2.

### Practical notes for the AT path
- **Stop *and disable* ModemManager** (`sudo systemctl disable --now ModemManager`) so it stops
  holding the AT port (it can't help — PPP is dead). Your code then owns `/dev/ttyUSB1`.
- **`mmcli --command` (raw AT) is blocked** — needs ModemManager "debug mode" *even with sudo*
  (`Operation only allowed in debug mode`). Don't fight it: stop MM and use **pyserial**
  directly (`sudo apt install python3-serial`; system Python is externally-managed, no pip).
- **`AT+CMQTTTOPIC`/`CMQTTPAYLOAD` use a `>` prompt:** send the command with the byte length,
  wait for `>`, then write **exactly N raw bytes (no CR)**. Write a **response-driven** driver
  (read until `OK`/`ERROR`/URC), never blind `sleep`s.
- On boot / before sending, ensure the data context: `AT+CGACT=1,1`.
- Harmless quirk: `AT+CMQTTSTOP` may return `ERROR` if the session was already torn down by
  `AT+CMQTTDISC` — the publish already succeeded.
- **acoupi integration:** because there's no OS network interface, acoupi's stock
  `paho-mqtt`/`requests` messengers **can't** run over cellular directly. Wrap the AT sequence
  in a concrete `Messenger` (a "cellular gateway") behind the offline-first queue instead.

---

## 7. Debug console (UART) — for when it won't boot at all

The only window into a board that won't finish booting (shows bootloader/kernel logs).

- On the **JCTL** connector, **1.8 V logic**, **115200 8N1**.
- **Needs a 1.8 V-capable USB-TTL adapter** (Arduino recommends **DSD TECH SH-U09C5**).
- **A plain 3.3/5 V CH340** (YP-05, DollaTek, etc.) is **over-voltage** — never drive its TX
  into the 1.8 V pin. Read-only (board-TX → adapter-RX, GND↔GND, nothing else) is *marginal*
  and won't damage anything, but is unreliable. Use 1.8 V or a level shifter.
- **ST-Link V2 is not a UART bridge** — it can't do this.
- **RS485 adapters are not TTL** — wrong signalling entirely; do not use.
- The exact JCTL pinout (which pins are console TX/RX/GND vs the EDL pins) is in the official
  "UNO Q full pinout" PDF — confirm pins before connecting.

---

## 8. Bonus: offline image customization (headless pre-provisioning)

Proven workflow for baking config/scripts into an image before flashing — useful for
building "golden" images for fleet/field devices:

1. `arduino-flasher-cli download latest` → get the `.tar.zst`.
2. Extract; the rootfs is `disk-sdcard.img.root` (**ext4**); `/home` is `disk-sdcard.img.home`;
   boot/ESP is `disk-sdcard.img.esp` (FAT).
3. Edit the rootfs **offline and rootless with `debugfs`** (no mount/sudo needed for ext4):
   add files, `set_inode_field` for perms/owner, `symlink` to enable systemd units.
4. `e2fsck -fn` to verify integrity, then `flash <extracted-folder>`.

Notes:
- **Match perms/ownership** — SSH and systemd silently ignore wrong-perm files. (We forgot
  host keys once and sshd wouldn't start.)
- `rawprogram*.xml` has `readbackverify="false"` and no per-file hash → **modified images
  flash fine**.
- Baked secrets (WiFi PSK, keys) are **plaintext in the rootfs** — OK for personal/dev; for a
  fleet, inject at **first boot** from a secure source instead.
- Don't fight App Lab: pre-baked ssh/wifi is best for **headless** boards; if you also use
  App Lab it may re-provision over your config.

---

## 9. SSH `known_hosts` after a reflash

Every reflash regenerates the board's host keys, so any machine that connected before gets:
```
WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!
```
This is **not** an attack — just a stale cached key (often compounded by DHCP reusing an IP).
Fix on each machine that connects:
```bash
ssh-keygen -R <ip>
ssh-keygen -R <hostname>.local
```
then reconnect. (Root login still only works from a machine holding the matching private key;
otherwise use the `arduino` user + its App-Lab password.)

---

## 10. A sane bring-up order (so you don't repeat our two days)

1. **Power it from a dumb 5 V/3 A supply** (not a PD/adaptive charger). Watch for figure-8 → heart.
2. If it won't reach the heart → it's power. Swap the supply before anything else.
3. To reach it first time: **cable to the PC** (adequate power) → find it via **ADB**.
4. Provision with **App Lab** (sets password, WiFi, SSH/host keys) — complete every screen.
5. Clear stale `known_hosts` entries, then `ssh arduino@<host>.local`.
6. Only suspect hardware/RMA after the console UART (§7) shows resets/panics on **good power**.
7. To reflash/recover: EDL (§4) → **`sudo` flash** → verify it actually wrote.
