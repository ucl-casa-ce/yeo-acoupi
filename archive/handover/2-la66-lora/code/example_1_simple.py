"""Example 1 — the simplest thing: send one message, then quit.

Talks to the LA66 radio over the UNO Q's built-in socket (that's the default, no
setup). This assumes the radio already has its keys (the LA66 remembers them
between runs). If it doesn't yet, do example 2 first.

Run it on the UNO Q:
    python example_1_simple.py

If it prints a line starting with "sent:", it worked.
"""
from lorawan import LA66, build_beacon

# LA66() with no arguments uses the UNO Q's :7500 socket to reach the radio.
# The "with" block connects on the way in and closes on the way out.
with LA66() as la:
    result = la.send(build_beacon(tick=1))   # blocks until the radio confirms it went out
    print("sent:", result)
