# Uno Q + Dragino LA66 → LoRaWAN (LORIOT / TTN) Setup Runbook

How to configure an **Arduino Uno Q + Dragino LA66 shield** to send LoRaWAN uplinks,
and how to repoint it between network servers (TTN ⇄ LORIOT) and activation modes
(OTAA ⇄ ABP). Written so it works for a **proper multi-channel gateway (OTAA — the
default)** and for a **single-channel gateway (ABP — special case)**.

---

## 0. Hardware & data path (why things are the way they are)

The Uno Q has two brains:
- **Qualcomm Linux core** — where you get a shell (`arduino@yeo-unoq-lora-1`).
- **STM32 MCU** — runs the Arduino passthrough sketch.

The Linux core has **no direct UART** to the headers, so all LA66 traffic routes:

```
Linux script → loopback socket 127.0.0.1:7500 → arduino-router → MCU passthrough
              → pins 0/1 (hand-wired) → LA66 shield
```

The MCU sketch (`lora-linux-bridge.ino`) is a one-byte-at-a-time pump; the two
directions are **independent** (this matters in §7).

> **Wiring note:** TXD→Pin 1, RXD→Pin 0 (per the shield readme — *not* the usual
> TX→RX crossover). It's hand-wired with DuPont jumpers, so the link is electrically
> marginal — reseat these first if comms are flaky.

---

## 1. Decide your path

| Your gateway | Activation | Channels | ADR | Notes |
|---|---|---|---|---|
| **Multi-channel** (normal LoRaWAN gw) | **OTAA** (recommended) | all band channels | **on** | The default. Join works because the gw can send the Join-Accept downlink. |
| **Single-channel** (basic/DIY gw) | **ABP** | lock to the gw's one channel | **off** | OTAA can't join (no reliable downlink); gw listens on one freq+SF, so lock them. |

Everything below is shared **except §6 and §7**, which split into **(a) OTAA** and
**(b) ABP**.

---

## 2. Get a shell on the board

**Over Wi-Fi (preferred once networked):**
```bash
ssh arduino@<board-ip>             # e.g. 192.168.68.125
ssh arduino@yeo-unoq-lora-1.local  # mDNS also works
```

**Over USB via ADB (fallback when the network is down/unknown):**
The Uno Q exposes an ADB interface over USB-C (`VID 2341`).
```bash
adb devices && adb shell
```
> **Gotcha:** it's Debian Linux, *not* Android — no `/data/local/tmp`; push to
> `/home/arduino/`. On Windows Git-Bash, set `MSYS_NO_PATHCONV=1` or adb mangles
> remote paths.

---

## 3. Add a Wi-Fi network (non-destructive)

NetworkManager keeps each network as its own profile; adding one does **not** disturb
the others (e.g. the home `CE-Hub-Staff`).
```bash
nmcli --ask device wifi connect <SSID>     # passphrase prompt is hidden
nmcli -f DEVICE,STATE,CONNECTION device status
ip -brief addr show wlan0                  # note the new IP
```
> **Gotchas:** the board's IP changes per network; re-discover after moving. Guest
> networks may isolate clients (blocks laptop→board SSH) — test with
> `Test-NetConnection <ip> -Port 22`.

---

## 4. (Aside) USB-C role & the SD-card-on-dongle gotcha

The single USB-C port is **dual-role** and can't do both jobs at once:
```bash
cat /sys/class/usb_role/4e00000.usb-role-switch/role   # "device" or "host"
```
- **`device`** — peripheral to a PC (ADB/serial). Won't power a USB SD reader →
  card absent from `lsblk`.
- **`host`** — can enumerate USB storage; reached by **powering the board through
  the dongle**. A USB-dongle card then shows as **`/dev/sdX`** (the internal eMMC is
  `mmcblk0`, model `BUTC42` — *not* the SD card).

---

## 5. Talk to the LA66

```bash
python3 ~/la66_socket.py     # board file uses UNDERSCORES
```
> **Gotchas:**
> - Run **on the board**, not the laptop — `127.0.0.1:7500` is the *board's* bridge
>   (`WinError 10061` = you ran it on Windows).
> - Only **AT commands** here; shell commands return harmless `AT_ERROR`.
> - Keys are entered **MSB-first** — paste exactly as the network server shows them.

---

## 6. Register the device

### 6a. OTAA (multi-channel — default)
In LORIOT/TTN: **Enroll Device → OTAA**. You use three values:

| OTAA value | Bytes | Source |
|---|---|---|
| **DevEUI** | 8 | device sticker (`A840…`) or server-generated |
| **AppEUI / JoinEUI** | 8 | the application's EUI |
| **AppKey** | 16 | server-generated root key |

No DevAddr/session keys — those are negotiated at join. Set region **EU868**.

### 6b. ABP (single-channel)
In LORIOT/TTN: **Enroll Device → ABP** (identify by DevEUI). The server mints:

| ABP value | Bytes |
|---|---|
| **DevAddr** | 4 |
| **NwkSKey** | 16 |
| **AppSKey** | 16 |

Set region **EU868**.
> **Gotcha (ABP only):** disable the server's **frame-counter check** (or note its
> **Reset** button). ABP can't renegotiate counters; if the device counter ever drops
> below the server's, uplinks are silently dropped. (The LA66 persists its counter
> across `ATZ`/reboot — only `AT+FDR` resets it — so in practice this bites only after
> a factory reset or counter desync.)

---

## 7. Configure the LA66

Type each line in `la66_socket.py`, Enter after each.

### 7a. OTAA profile (multi-channel — default)
```
AT+NJM=1                       # OTAA
AT+DEUI=<DevEUI>               # usually factory-set already; set to be sure
AT+APPEUI=<AppEUI/JoinEUI>
AT+APPKEY=<AppKey>
AT+ADR=1                       # ADR on — fine with a real gateway
AT+CHS=0                       # 0 = use all band channels (disable single-channel)
AT+CLASS=A
ATZ                            # reboot → device sends a JoinRequest and JOINs
```
After reboot, watch for a **`JOINED`** message; confirm with `AT+NJS` (join status).

### 7b. ABP profile (single-channel)
```
AT+NJM=0                       # ABP
AT+DADDR=<DevAddr>
AT+NWKSKEY=<NwkSKey>
AT+APPSKEY=<AppSKey>
AT+ADR=0                       # MUST be off, or it drifts off the gw's fixed SF
AT+DR=5                        # DR5 = SF7/125 kHz (match the gw's SF)
AT+CHS=868100000               # lock to the gw's single channel (868.1 MHz)
AT+CLASS=A
AT+CFM=0                       # unconfirmed uplinks
ATZ
```
No join happens (ABP is "joined" immediately).

> **Gotcha — reading config back (`AT+CFG`):** the LA66 interleaves its async TX/RX
> debug (`Start Tx event`, `wait for the erase to complete`) into any reply, garbling
> it. It's **worst during transmit storms** — e.g. an OTAA device out of range
> retries joins constantly. When the device is idle between uplinks you can usually
> catch a clean `AT+CFG` in the gap. Either way the robust method is: **send config
> "blind" (your input is unaffected — the bridge's two directions are independent) and
> verify end-to-end at the network server (§8).**

---

## 8. Test uplink & verify

From the same console:
```
AT+SENDB=0,2,2,00FA
```
Format: `AT+SENDB=<confirm=0>,<FPort=2>,<len=2>,<hexdata=00FA>`. Healthy output:
```
***** UpLinkCounter= N *****
TX on freq 868.100 MHz at DR 5     ← confirms channel + SF (single-channel case)
OK
txDone
RX … rxTimeout                     ← no downlink; normal for unconfirmed
```
**Verify on the network server** (LORIOT/TTN live data):
- **OTAA:** device shows **Joined**, then the uplink (payload `00FA`, port 2).
- **ABP:** uplink appears immediately from your **DevAddr**.

If LORIOT decrypts the frame, all keys went down the wire correctly.

> **If nothing arrives:** re-send the key lines (a dropped byte on the hand-wired UART
> is the prime suspect) → confirm the gateway is **Connected** on the server → check
> the **antenna** (never TX without it) → (single-channel) confirm `TX on freq` matches
> the gw's channel/SF.

---

## 9. Run it automatically (cron)

The auto-sender is a commented-out cron line; remove the leading `#`:
```bash
crontab -e
```
```
* * * * * /usr/bin/python3 /home/arduino/la66_ttn-test.py >> /home/arduino/la66_cron.log 2>&1
```
> The script is *named* `ttn` but only calls `AT+SENDB`, so it delivers to whatever
> network the radio is currently configured for. Every-minute SF7 uplinks are well
> within the EU868 1% duty cycle. Slow it with `AT+TDC` if needed.

---

## 10. Switching network / mode later

Keys are stored per-mode (OTAA set and ABP set are separate), and `AT+NJM` just
*selects* which is active — so switches are reversible.

- **ABP/single-channel → OTAA/multi-channel:**
  ```
  AT+NJM=1
  AT+CHS=0          # restore all channels
  AT+ADR=1          # re-enable ADR
  AT+APPEUI=… / AT+APPKEY=…   # set if changing network server
  ATZ               # → JOINs
  ```
- **TTN ⇄ LORIOT:** set the target server's keys (OTAA: AppEUI/AppKey; ABP:
  DevAddr/NwkSKey/AppSKey), keep the same DevEUI, `ATZ`.

> **Never run `AT+FDR`** — it factory-resets the module (erases keys, resets the frame
> counter). It's the only thing that does.

---

## Consolidated gotchas

**General (any gateway):**
1. `AT+CFG` replies garble (async-debug interleave) — configure blind, verify at the server. Time clean reads for the idle gaps between uplinks.
2. Run `la66_socket.py` **on the board**; board filenames use **underscores**.
3. Keys are **MSB-first** — paste as the server shows them.
4. **Never `AT+FDR`.** `AT+NJM` switches mode without erasing stored keys (reversible).
5. **OTAA vs ABP credentials are different sets:** DevEUI/AppEUI/AppKey (OTAA) vs DevAddr/NwkSKey/AppSKey (ABP).
6. One USB-C port, dual-role — can't PC-tether and host the SD dongle at once.
7. ADB-over-USB is the reliable fallback when the network is down.
8. Don't type shell commands into the AT console (harmless `AT_ERROR`).
9. Antenna must be attached before any transmit.
10. EU868 sanity check: the LA66's RX2 window prints `869.525 MHz`.

**Single-channel / ABP only:**
11. OTAA won't join through a single-channel gw (no reliable Join-Accept downlink) → use ABP.
12. Lock the channel (`AT+CHS=<freq>`) and SF (`AT+DR=5`), and turn **ADR off** (`AT+ADR=0`) or it drifts off the gw's fixed SF.
13. Disable the server's frame-counter check (or use its Reset) — ABP can't renegotiate counters.

---

## Reference

- LA66 LoRaWAN Shield User Manual:
  https://wiki-old.dragino.com/xwiki/bin/view/Main/User%20Manual%20for%20LoRaWAN%20End%20Nodes/LA66%20LoRaWAN%20Shield%20User%20Manual/
- AT command names follow Dragino's standard set; verify against your firmware's
  `AT+CFG` output or the manual if a command is rejected.
