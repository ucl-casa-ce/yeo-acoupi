# How `lorawan` works

A clean, robust LA66 LoRaWAN driver for the UNO Q. It consolidates the
field-proven `lora.py` (raw `:7500` socket, reconnect, background reader,
duty-cycle pacing, crash-durable spool) and adds the two things it lacked for
a multi-network interface: a **transport seam** and **OTAA join/provisioning**.

This doc explains the shape and every part. Companion: `../board_pull/lora.py`
is the original it descends from.

---

## 1. The problem, and why the design is shaped this way

The LA66 is a LoRaWAN radio you drive with **AT commands** over a 9600-baud
serial line. On the UNO Q, that serial line (D0/D1) belongs to the **STM32**,
not Linux — so a Linux process reaches the LA66 only *through* the MCU. There
are three ways bytes can get there, and the driver must not care which:

```
Linux (this driver)  ──▶  [ a Link ]  ──▶  LA66 AT interface  ──RF──▶ gateway ──▶ network server
                          socket :7500          (LoRaWAN radio)                    (LORIOT/TTN/custom)
                          USB serial
                          fake (tests)
```

Two design consequences fall out of that picture, and they're the whole reason
this is more than a 30-line script:

- **Transport is pluggable (the `Link`).** The proven path is arduino-router's
  transparent `:7500` tunnel to the STM32; but the same driver should also run
  over a direct USB serial (the Dragino LA66 *USB adapter*) and over an
  in-memory fake so the logic is testable without a radio. So all byte I/O
  hides behind a `Link`.
- **The network server is a *config* choice, not a code choice.** LORIOT, TTN,
  and a custom server all speak standard LoRaWAN — what differs per-server is
  the device's **activation** (OTAA vs ABP) and **keys**. So the driver
  surfaces provisioning + join, and nothing about LORIOT-vs-TTN appears in the
  code path.

---

## 2. The `Link` seam

```python
class Link(ABC):
    def open(self):  ...            # connect / reconnect
    def send(self, data: bytes):    # write raw bytes
    def recv(self, timeout) -> bytes:  # up to N bytes, or b"" within timeout
    def close(self): ...
```

Three implementations ship:

| Link | Transport | When |
|---|---|---|
| `SocketLink` | arduino-router `127.0.0.1:7500` | the framework-free UNO Q path (default, field-proven — put FCnt 8 on TTN) |
| `SerialLink` | pyserial `/dev/ttyUSB0` | the Dragino LA66 **USB adapter**, or any board where Linux owns the UART |
| `FakeLink` | in-memory scripted replies | tests — no hardware |

A `Link` only moves bytes; it knows nothing about AT commands. That means the
robust machinery (reconnect, reader thread, duty cycle, spool) is written
**once** in the driver and works over every transport. `FakeLink` (in
`test_lorawan.py`) is what lets the entire driver — including the reader
thread, join polling, and txDone gating — run in 0.6 s with no radio.

> **Why not a `BridgeLink` for the in-app Arduino RPC path?** The Bridge only
> works from the single App-loop thread (documented gotcha), which is
> fundamentally incompatible with this driver's background reader + spool
> worker threads — and re-couples to the ~270 MB framework this design is
> leaving. So Bridge is intentionally out; `SocketLink` supersedes it.

---

## 3. The driver, part by part

### Exceptions
One base `LoRaError` with typed subclasses so callers can react precisely:
`LoRaConnectionError` (link down), `LoRaTimeout` (no reply), `LoRaModuleError`
(the LA66 said `AT_ERROR`/`AT_BUSY`/…), `LoRaDutyCycle` (budget not yet
available), `LoRaJoinError` (OTAA join didn't complete).

### `LA66.__init__` / `connect()`
Holds a `Link` (defaults to `SocketLink()`), plus the duty-cycle policy and a
non-reentrant `RLock` that serializes every AT transaction. `connect()` opens
the link, starts the **background reader thread**, and does a warm-up.

### The background reader (`_read_loop`)
The LA66 sends lines asynchronously — solicited replies *and* unsolicited URCs
(`txDone`, `TX on freq …`). A dedicated thread continuously `recv()`s from the
Link, splits on `\n`, and puts complete lines onto a thread-safe `queue`. This
is why bytes are never lost between commands: something is always draining the
pipe. A fresh queue is created on every (re)connect so stale lines from a dead
socket can't leak across.

### `at()` — one AT transaction
The workhorse, under the lock: drain any stale lines, write `cmd\r\n`, then
pull lines off the queue until a **terminator** (default `OK`) — raising
`LoRaModuleError` on an error token or `LoRaTimeout` on a deadline. The
terminator is overridable, which matters because different commands "finish"
differently: a send finishes on `txDone`, a config read on `OK`. Every read is
bounded by `time.monotonic()`.

### Reconnect (`_ensure_connected` / `_reconnect`)
If the reader thread has died (link dropped), the next `at()` transparently
reopens the Link with exponential backoff and re-warms-up, then retries the
write once. Callers don't see transient link loss.

### Provisioning + activation — the multi-network part
```python
la.provision_otaa(dev_eui="A840…", app_key="2B7E…", app_eui="0000…")
la.join()                       # or: la.provision_abp(dev_addr, nwkskey, appskey)
```
- `provision_otaa` sets `AT+NJM=1` (OTAA) then `AT+DEUI/APPEUI/APPKEY`. The
  `app_eui` (JoinEUI) defaults to all-zeros, which is what TTN and LORIOT use.
- `provision_abp` sets `AT+NJM=0` then `AT+DADDR/NWKSKEY/APPSKEY`.
- Credentials pass through `_hex()`, which validates length + hex-ness up front
  (a wrong key length fails loudly, not silently on air).

### `join()` — robust OTAA
```python
if self.joined(): return
issue AT+JOIN
poll AT+NJS until it reads 1, or timeout; retry AT+JOIN up to `retries`
raise LoRaJoinError if never joined
```
The key robustness choice: success is confirmed by reading **`AT+NJS`** (join
status = 1), **not** by matching a firmware-specific "JOINED" banner string.
Different LA66 firmware revisions word the join reply differently; the status
register doesn't. `joined()` returning True for ABP (always joined) keeps the
send path uniform.

### `configure()` — the deployment posture (and a fixed bug)
```python
la.configure(dr=5, adr=True)                     # full plan + ADR (deployment)
la.configure(single_channel_hz=868_100_000, dr=0)  # home single-channel test
```
- **Full plan is the deployment default** (`AT+CHS=0`) with **ADR on** — let
  the network optimize the data rate across all channels. This is the opposite
  of the old single-channel-pinned `main.py`, which was a home-gateway hack.
- **Single-channel** pins one frequency and **forces ADR off + a fixed DR**
  (ADR is meaningless on one channel).
- A data-rate change updates `self.sf` so the **duty-cycle airtime model stays
  correct**.
- **The fix:** any channel-plan change issues **`ATZ`** afterward, because
  `AT+CHS` only takes effect after a module reset — the original `lora.py`
  omitted this, so a plan change could silently not stick. After `ATZ` the
  driver re-warms-up to absorb the reboot bytes.

### Duty cycle (`_pace` / `_register_tx`)
EU868 permits 1% air time. After each send the driver computes the frame's true
airtime (Semtech formula, `lora_airtime_s`) and sets `_next_ok = now + airtime
× (1/dc − 1)`. The next send either **waits** until then (default) or **raises
`LoRaDutyCycle`** (if `duty_cycle_block=False`, for a non-blocking caller). This
is what stops an unattended node from getting itself rate-limited or breaking
regulations.

### `send()` — one uplink, txDone-gated
Build `AT+SENDB=<confirm>,<fport>,<len>,<hex>`, pace for duty cycle, then run
`at(..., terminators=("TXDONE",))` and parse the reply for FCnt / freq / DR into
a `SendResult`. `confirm=True` requests a real **network ACK** (needs a
multi-channel gateway that can send the downlink); `confirm=False` is
fire-and-forget (only `txDone` = "transmitted"). Transient failures
(connection loss, `AT_BUSY`) are retried with backoff.

### `DiskSpoolSender` — crash-durable queue
The shape the inference pipeline should use. `enqueue(payload)` writes the
message to a spool **file** (atomic rename) and returns immediately; a worker
thread drains the spool oldest-first, respects the duty cycle, and deletes each
file only after a confirmed `txDone`. Messages survive a process crash;
permanently-failing ones are moved to `<name>.failed` rather than lost or
retried forever.

---

## 4. End to end: provision a device for TTN, then tick

```python
from lorawan import LA66, SocketLink, build_beacon

la = LA66(SocketLink())            # framework-free :7500 path
la.connect()
la.provision_otaa(dev_eui=DEV_EUI, app_key=APP_KEY)   # TTN/LORIOT/custom: same call
la.join()                          # AT+JOIN → polls AT+NJS=1 → returns
la.configure(dr=5, adr=True)       # full EU868 plan, ADR on
res = la.send(build_beacon(tick=1))   # duty-cycle paced, waits for txDone
print(res)                         # SendResult(ok=True fcnt=… freq=… dr=… …)
```
Pointing the same device at LORIOT or a custom server instead of TTN changes
**only** the keys you pass to `provision_otaa` (and where you register the
device) — no code changes, because the device just does standard LoRaWAN.

---

## 5. How the tests fake the radio

`test_lorawan.py` gives `FakeLink` a `responder(cmd) -> list[str]` — a function
mapping each AT command to the lines the LA66 would answer. `send()` pushes the
mapped reply bytes into a buffer that `recv()` yields, so the **real reader
thread, queue, and `at()` parser** all run unchanged. Examples the suite pins:

- `AT+SENDB…` → `["…UpLinkCounter= 7 …", "TX on freq 868.100 MHz at DR 0", "OK", "txDone"]` — verifies txDone gating + FCnt/freq/DR parsing.
- OTAA: `AT+NJS` returns `0` until `AT+JOIN`, then `1` — verifies `join()` polls to success (and raises `LoRaJoinError` when it never joins).
- `configure(full_channel_plan=True)` → asserts `AT+CHS=0`, `AT+ADR=1`, and `ATZ` are all issued.

19 tests, no hardware, ~0.6 s.

---

## 6. Status / caveats

- **Verified:** the full driver logic via `FakeLink`; airtime math (7-byte SF12
  = 1.319 s, matching the field notes); lint clean.
- **Not yet hardware-checked:** the OTAA command *tokens* (`AT+NJM/DEUI/APPEUI/
  APPKEY/JOIN/NJS`) are the standard Dragino set (the setup doc confirms
  `NJM`, `CHS`, `DR`, `SENDB`, `VER=EU868 v1.3`), but at build time the board's
  MCU wasn't relaying after a cold boot (`:7500` up, LA66 silent), so a live
  `AT+CFG`/`=?` read wasn't possible. **Do one hardware smoke test of
  `provision_otaa` + `join()` once the MCU is up** (`arduino-app-cli app start`,
  or confirm the pass-through self-runs) before trusting OTAA in the field.
- **Unconfirmed vs confirmed:** on a single-channel home gateway keep
  `confirm=False` (it can't return the downlink ACK). On a real multi-channel
  gateway use `confirm=True` for a genuine network-received guarantee.
