# LA66 on the UNO Q — RPC vs Raw: how we got here, and why we switched

This is the decision record for **how the UNO Q's Linux side drives the Dragino
LA66 LoRaWAN shield**. We built it twice: first on the Arduino **App + Bridge
RPC** model, then on a **raw socket + pass-through sketch**. This explains both,
what forced the change, and the measurements behind the call.

- **Runbook / setup steps:** [`UNO_Q_LA66_SETUP.md`](UNO_Q_LA66_SETUP.md)
- **Production client:** `lora.py` *(superseded by [`code/lorawan.py`](code/lorawan.py); the original is in the main repo at `unoq/lora/lora.py`)*
- **MCU sketch (raw path):** `lora-linux-bridge.ino`
- **Backend bridge (TTN → MQTT):** `ttn_mqtt_bridge.py` *(TTN-era, not in this pack — main repo: `bridges/`)*

---

## TL;DR — the decision

> **Use the raw-socket / pass-through path** (`lora.py` *(superseded by [`code/lorawan.py`](code/lorawan.py); the original is in the main repo at `unoq/lora/lora.py`)* + `lora-linux-bridge.ino`), **not** the Arduino App + Bridge RPC.

Because our real workload is a **standalone Python inference process** (bat/bird
detection) that must send an uplink when it detects something. The Bridge RPC
can only be called **from inside the Arduino App's own process**, so a separate
inference process **cannot use it**. The raw path is a plain TCP socket any
process can open, it sheds ~270 MB of App-framework RAM, and it has fewer moving
parts to fail — at the cost of a disciplined client, which is what `lora.py` is.

---

## 1. The goal

A complex Python program (an inference model + coordination logic) runs on the
UNO Q's **Linux** side. When it decides to report something, it needs to put a
**LoRaWAN uplink** on air via the stacked LA66 shield — from Linux, on the
device, with no internet at the device (that's the whole point of LoRa).

Downstream of the radio is solved and unchanged: `LA66 → gateway → TTN → bridge
→ MQTT`. This document is only about the **on-device send path**.

## 2. The physical constraint that shapes everything

On the UNO Q, **every Arduino header pin belongs to the STM32 MCU, not Linux.**
The LA66's UART is on **D0/D1**, so **Linux cannot open the LA66's serial port
directly.** Whatever we do, bytes must travel:

```
Linux  ──►  arduino-router  ──► /dev/ttyHS1 ──►  STM32 sketch  ──► Serial1 (D0/D1) ──► LA66
        (a local socket)      (internal UART)   (relays bytes)      9600 8N1
```

`arduino-router` is a **system service** that owns the internal Linux↔MCU UART
and exposes a **local socket on `127.0.0.1:7500`**. Think of `:7500` as a **dumb,
transparent tunnel** to the MCU's `Serial`. **What the bytes *mean* is decided
entirely by the sketch flashed on the MCU** — and that is the whole story of RPC
vs raw.

## 3. Approach 1 — Arduino "App" + Bridge RPC (where we started)

An Arduino **App** = a C++ **sketch** on the STM32 + a **Python** program on
Linux, linked by a msgpack **RPC** ("Bridge"). The sketch `provide()`s functions;
the Python side `Bridge.call("la66_send", …)` invokes them.

```
Arduino App (Python in a Docker container)
   Bridge.call("la66_send", "AT+SENDB=…")
      → arduino-router  (:7500 carries RPC frames)
         → STM32 sketch: Bridge.provide(la66_send / la66_drain)
            → Serial1 9600 → LA66 → RF → gateway → TTN → bridge → MQTT
```

**This works — we verified it end to end** (uplinks decoded on TTN and relayed to
an MQTT broker). But it has a fatal property for our use case and several
operational sharp edges we actually hit (see `UNO_Q_LA66_SETUP.md` §6):

- **`Bridge.call` only works from the App's own process, on its loop thread.** A
  separate process (or even a worker thread) gets empty replies / timeouts.
- `provide()`s registered only in `setup()` go **stale across a restart** that
  doesn't reflash → calls silently time out until you force a reflash.
- Replies only reach the App's process; `docker exec` / a second process see
  nothing.

### The wall

Our inference program is a **standalone Linux process**, not an Arduino App. So
it **cannot call the Bridge at all.** The only way to keep the RPC model would be
to run a *second* Arduino App as a "sender daemon" and invent an IPC channel from
the inference process to it — i.e. add a whole extra process + container + IPC
just to reach a socket that already exists.

## 4. Approach 2 — raw socket + pass-through sketch (where we landed)

Flash a **pass-through sketch** instead: it does nothing but relay bytes between
the MCU's `Serial` (the Linux link) and `Serial1` (the LA66). Now `:7500` carries
**raw AT bytes**, and **any process** can open the socket and talk to the LA66:

```
Inference process (standalone Python)
   import lora; lora.send(payload)          ← plain socket, no framework
      → socket 127.0.0.1:7500  (raw AT bytes)
         → arduino-router → STM32 pass-through (Serial ↔ Serial1)
            → Serial1 9600 → LA66 → RF → gateway → TTN → bridge → MQTT
```

`lora-linux-bridge.ino` in full is ~15 lines: open both UARTs at 9600, and in
`loop()` copy `Serial ↔ Serial1` byte for byte (plus a boot-time `INPUT_PULLUP`
window on D0 to clear the UNO Q's cold-boot floating-RX quirk).

**No `Bridge.call`, no App, no container, no loop-thread rule.** The "same
problem" from Approach 1 simply does not exist here.

## 5. The two flows side by side — what changes and why

Only **two** things change; everything else is identical.

| # | Change | From (RPC) | To (raw) | Why |
|---|---|---|---|---|
| 1 | Who can send | Only the App's loop thread | **Any** process | Removes the process-binding — the entire reason for switching |
| 2 | Client call | `Bridge.call(...)` | `socket.connect(('127.0.0.1', 7500))` | A socket has no App/thread constraint |
| 3 | MCU sketch | `Bridge.provide` (RPC) | raw pass-through | Turns `:7500` into a transparent AT pipe |
| 4 | App container | in the send path | **removed** | Not needed without the Bridge |
| — | `arduino-router`, `:7500`, `Serial1` 9600, LA66, RF, gateway, TTN, bridge, MQTT | **unchanged** | **unchanged** | The transport swap stops at the MCU |

`arduino-router` and `:7500` appear in *both* because they don't change — they're
the always-on tunnel. The migration is literally: **swap the sketch, swap the
client.**

## 6. Is one more robust or efficient?

### Efficiency — a tie on the wire, a win for raw on footprint

The bottleneck is the **radio**, not the transport. An SF12 uplink is **~1.5 s of
airtime**; the LA66 link is 9600 baud. RPC-framing vs raw-bytes differ by
microseconds against that — **send latency/throughput are identical.**

Where they differ is **resident RAM**, measured on the board:

| Component (App/RPC only) | RSS |
|---|---|
| `arduino-app-cli` (orchestrator daemon) | ~126 MB |
| `dockerd` | ~85 MB |
| `containerd` (+ shim) | ~42 + 18 MB |
| Python app, containerized | ~44 MB |
| **≈ total pulled in by the App model** | **≈ 270–315 MB** |

| Component shared by **both** | RSS |
|---|---|
| `arduino-router` (the tunnel) | ~10 MB |

So the raw path hands **~270 MB back** to the inference model and, if the App is
the only Docker workload, lets you **disable Docker entirely**. (The board has
3.6 GB total / ~2.9 GB free, so this is headroom to reclaim, not a present
crisis — but across a fleet of solar edge units it's a real, repeatable saving.
A headless field image can also drop the ~200 MB desktop stack: `Xorg` +
`lightdm`.)

### Robustness — depends on discipline, and we supply it

- **Out of the box, RPC is more structured** (framing, request/response). A raw
  socket is a dumb byte stream: no arbitration, no confirmation, no framing
  unless you build it. The naive sample scripts (connect-per-send, never read the
  reply, no lock) are therefore *less* robust than RPC.
- **Operationally, raw has fewer failure points.** The pass-through sketch is
  **stateless** — nothing to go stale (unlike the RPC `provide()` staleness we
  hit). Fewer layers = fewer independent things to break.

The right verdict: **with a disciplined client, raw is at least as robust as RPC
and simpler operationally.** That client is `lora.py` *(superseded by [`code/lorawan.py`](code/lorawan.py); the original is in the main repo at `unoq/lora/lora.py`)*, which restores
everything the RPC gave for free:

- **one** long-lived connection, **lock-guarded** → never interleaves commands;
- a background **reader thread** → replies are always drained;
- **waits for `txDone`** (or raises a typed error) → every send is confirmed;
- **EU868 duty-cycle pacing** from real LoRa airtime → won't get rate-limited;
- **auto-reconnect** with backoff, boot-byte **warm-up**;
- an optional **crash-durable disk spool** so detections survive a process crash.

> The robustness that matters most — queueing when duty-limited, retry/backoff,
> not losing a detection during a ~1.5 s TX — lives in `lora.py`, **not** in the
> choice of transport. The transport choice is really about footprint and
> operational simplicity, and both point to raw.

## 7. How to use it

```python
import lora

# one-shot, blocks until txDone (or raises):
lora.send(lora.build_beacon(tick=42, site=0))

# durable + non-blocking — the shape for the inference pipeline:
link = lora.get_link()
tx = lora.DiskSpoolSender(link, "/var/lib/yeo/loraspool")
tx.start()
...
tx.enqueue(lora.build_beacon(tick=42))   # returns immediately; survives a crash
```

`build_beacon(tick, site)` produces the tagged 7-byte payload
(`magic·tag·tick(u32 LE)·site`) that the TTN uplink decoder and `common.py` ingest
expect. CLI smoke test: `python3 lora.py 7`.

## 8. Migration steps (RPC → raw)

1. **Flash** `lora-linux-bridge.ino` to the STM32 (replaces the RPC `provide`
   sketch). Verify the pins: LA66 `TXD → D0`, `RXD → D1`.
2. **Stop / don't run** the Arduino App that owned the LA66 — only one master may
   drive the link (`arduino-app-cli app stop user:la66`). Optionally disable
   Docker + `arduino-app-cli` on a production image to reclaim the RAM.
3. Confirm `arduino-router` is up and `127.0.0.1:7500` is listening
   (`ss -tulpn | grep 7500`).
4. `python3 lora.py 1` → expect an `AT+CFG` dump then a confirmed `txDone`.
5. Confirm the frame on **TTN Live data** and on the **MQTT broker** (via
   `ttn_mqtt_bridge.py`), exactly as we validated the RPC path.

## 9. When the RPC model would still be preferable

For completeness — keep the App + Bridge model if you want the Arduino **App
ecosystem** (App Lab UI, lifecycle management, the examples), or if your Linux
code is *already* naturally the App's `main.py` and never needs to send from a
separate process. For a standalone, resource-conscious inference service, raw
wins.
