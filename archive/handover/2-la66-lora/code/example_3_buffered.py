"""Example 3 — the pattern you'd actually ship: never block, survive crashes.

Your app calls enqueue() and moves on. The message is written to disk first, so
it survives a crash or a power cut. A background worker sends the queue in order,
automatically waits the legally-required gaps between transmissions, and only
deletes a message once the radio confirms it actually went out.

Assumes the radio is already set up with its keys (see example 2).

Run it on the UNO Q:
    python example_3_buffered.py
"""
import time
from lorawan import LA66, DiskSpoolSender, build_beacon

la = LA66()                     # uses the :7500 socket
la.connect()

# The background sender drains an on-disk queue.
sender = DiskSpoolSender(la, "/home/arduino/.lora_outbox")
sender.start()

# Your app just enqueues — instant, never waits on the radio.
for tick in range(1, 4):
    sender.enqueue(build_beacon(tick=tick))
    print("queued tick", tick)

# The worker sends them in the background. LoRa is slow and rate-limited, so give
# it time (a few minutes at the longest range setting). In a real app you'd just
# leave `sender` running for the life of the process instead of sleeping.
time.sleep(60)

sender.stop()
la.close()
