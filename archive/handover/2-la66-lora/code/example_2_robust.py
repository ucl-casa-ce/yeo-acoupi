"""Example 2 — the full path: set the keys, join the network, then send.

Use this the first time a device talks to a given network (TTN / LORIOT / your
own server). The nice part: it's the *same* code for all of them — only the keys
change, because they all speak standard LoRaWAN.

Run it on the UNO Q:
    python example_2_robust.py
"""
import lorawan
from lorawan import LA66, build_beacon

# ---- fill these in (from your network server's device page) ----
DEV_EUI = "A840000000000000"                    # this device's EUI
APP_KEY = "2B7E151628AED2A6ABF7158809CF4F3C"    # the app key
APP_EUI = "0000000000000000"                    # TTN & LORIOT both use all-zeros
# ----------------------------------------------------------------

la = LA66()                     # uses the :7500 socket
try:
    la.connect()
    la.provision_otaa(dev_eui=DEV_EUI, app_key=APP_KEY, app_eui=APP_EUI)
    la.join()                                # AT+JOIN, waits until it's actually joined
    la.configure(dr=5, adr=True)             # full channel plan; let the network tune the rate
    print("sent:", la.send(build_beacon(tick=1)))

# Each failure has its own type, so you can react to the ones you care about:
except lorawan.LoRaJoinError as e:
    print("couldn't join — check the keys and that a gateway is in range:", e)
except lorawan.LoRaModuleError as e:
    print("the radio rejected a command:", e)
except lorawan.LoRaConnectionError as e:
    print("lost the link to the radio:", e)
except lorawan.LoRaError as e:
    print("lora problem:", e)
finally:
    la.close()
