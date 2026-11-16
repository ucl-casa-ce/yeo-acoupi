# Send data over LoRaWAN, from Python

This kit lets a Python script send data over the **LA66 LoRaWAN radio** on the
Arduino UNO Q. You don't need to know anything about AT commands or radios —
that's all hidden inside `lorawan.py`. You say "join the network" and "send this".

## What's in here

- **`lorawan.py`** — the bit you import. Talks to the radio, joins the network,
  sends messages, and handles all the fiddly bits (retries, legal air-time limits,
  reconnecting, crash-proof queueing). This is the whole interface.
- **`test_lorawan.py`** — a test suite that runs the whole thing against a *fake*
  radio, so you can check it works **with no hardware at all**.
- **`example_1_simple.py`** — send one message. Start here.
- **`example_2_robust.py`** — set the keys, join the network, send, with proper
  error handling.
- **`example_3_buffered.py`** — the version you'd actually ship: queue messages to
  disk and let a background worker send them.
- **`HOW_IT_WORKS.md`** — the deep dive, if you want to know how it's built.

## Try it with no hardware first

You can prove the whole thing works before touching a radio — the tests run it
against a fake:
```
pip install pytest
python -m pytest test_lorawan.py -q
```
You should see `19 passed` in about a second.

## Running it for real (on the UNO Q)

The examples run **on the UNO Q itself** (that's where the radio is wired in).
By default the code reaches the radio over a local socket the board already
provides — no configuration needed. One requirement: the small pass-through
program has to be loaded on the board's microcontroller so Linux can reach the
radio (that's a one-time board setup — see the setup runbook).

Then, from this folder on the board:
```
python example_1_simple.py
```

There are **no Python dependencies** for the normal path. (You only need
`pyserial` if you plug the radio in over USB instead — that's the `SerialLink`
option.)

## Which example do I copy?

- Just trying it out (radio already has its keys) → **example 1**.
- First time on a network, or building something real → **example 2** (it sets the
  keys and joins).
- Putting it in a deployment → **example 3**'s queue-to-disk approach, so a crash
  or a dropped signal never loses a message.

## A few things worth knowing

- **Same code for any network.** TTN, LORIOT, or your own server all speak
  standard LoRaWAN — only the keys you paste into example 2 change.
- **It waits between sends on purpose.** LoRa has a legal limit on how much you can
  transmit (about 1% of the time). The library paces itself so you can't break
  that or get your device throttled — which is why sends can take a while.
- **`confirm=True` vs `confirm=False`.** `confirm=True` asks the network to send
  back an acknowledgement — only works if you have a proper multi-channel gateway.
  On a simple single-channel home gateway, leave it `False`.
- **Errors are specific** — `LoRaJoinError` (couldn't join), `LoRaModuleError` (the
  radio refused a command), `LoRaConnectionError` (lost the link), `LoRaDutyCycle`
  (need to wait before sending again). Catch the ones you care about.

## Heads-up (please read once)

The everyday send path here is field-proven. The **join step** (`join()` /
`provision_otaa` in example 2) has passed all the software tests but hasn't yet
been confirmed on a real radio end-to-end — so do **one** live join on the bench
before you rely on it in the field. If your device uses fixed ABP keys, or is
already joined, this doesn't apply.
