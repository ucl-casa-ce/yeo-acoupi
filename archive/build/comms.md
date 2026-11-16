> Note: summary of AI-assisted technical review using UCL AIRIA (Claude Sonnet 4.6)

# Lora / Cellular notes
**Project:** YeoValley WAMPAM (Woodland and Meadow Passive Acoustic Monitoring)  
**Location:** Northern escarpment of the Mendip Hills, Somerset (51.2986°N, 2.7058°W)  
**Date:** June 2026   

---

## 1. Project Overview

### Deployment
- **12× Raspberry Pi bioacoustics monitors** running the **acoupi Python framework**
- Detecting **bird and bat calls** using BirdNET / BatDetect2 ML inference
- **10-byte LoRaWAN payload** per detection event (species call + confidence)
- Sensors spread over **~4 km** with **~200 m elevation change**
- Site: **Mendip Hills escarpment** — steep north-facing scarp dropping from plateau
  (~250 m) to Yeo Valley / Blagdon Lake floor (~70–100 m)
- Terrain: **Deciduous woodland** (ash, oak, hazel) on scarp face — significant
  seasonal signal attenuation (worst case summer full canopy: up to 30 dB extra loss)

### Connectivity Options Under Evaluation
1. **LoRaWAN** — Multitech Conduit Gateway → TTN or Loriot
2. **Cellular (4G LTE / LTE-M / NB-IoT)** — direct MQTT to own broker
3. **Hybrid** — LoRaWAN for some nodes, cellular for others

### Computing Hardware Under Evaluation
- **Raspberry Pi 5** (existing familiarity)
- **Arduino UNO Q** (new Qualcomm-acquired platform, lower power)

---

## 2. Site Analysis — Mendip Hills Escarpment

### Terrain Profile
```
SOUTH (plateau)      ESCARPMENT           NORTH (valley)
~250–280 m          drops ~200 m          ~70–100 m
[Mendip plateau] → [steep wooded face] → [Yeo Valley / Blagdon Lake]
     ↑                    ↑                      ↑
Sensor cluster A    Sensor cluster B       Sensor cluster C
(open farmland)     (woodland edge)        (lakeside/wetland)
```

### Key Ecological Context
- Classic bat habitat: Greater/Lesser Horseshoe Bats, Natterer's, Pipistrelles
- Blagdon Lake (Bristol Water reservoir) immediately north — key foraging habitat
- Deciduous woodland = **seasonal signal variation** (bare winter → full canopy summer)

---

## 3. LoRaWAN Network Design

### Link Budget Summary (EU868 / 868 MHz)

| Parameter | Value |
|---|---|
| Max TX power (EU868) | +14 dBm |
| SF12 receiver sensitivity | −137 dBm |
| Total link budget (SF12) | ~155–160 dB |
| Practical usable path loss | ~140–145 dB |
| Fresnel zone radius at 2 km | ~17–18 m |
| Dense woodland extra loss | 20–50 dB |

### Traffic Engineering
- Payload: 10 bytes — ideal for LoRa
- Time on air (SF9, 10 bytes): ~185 ms
- EU868 1% duty cycle: up to ~190 packets/hour per device
- Single 8-channel Conduit: capacity not a concern at 12 devices

### Spreading Factor Guidance

| Scenario | Recommended SF |
|---|---|
| Clear LoS, <2 km | SF7–SF9 |
| Partial woodland, 2–4 km | SF9–SF11 |
| Dense woodland, obstructed | SF11–SF12 |
| Summer full canopy | SF12 (worst case) |

### Gateway Placement Recommendations

#### Option 1 — RECOMMENDED: Single Gateway, Escarpment Top (~250 m)
- Position at **rim of Mendip escarpment** (~51.29–51.30°N, 2.70–2.72°W)
- Looks DOWN over all valley and escarpment sensors
- Candidate locations: Ubley Warren Farm area, Compton Martin ridge,
  Blagdon Hill top
- **Antenna:** 3 dBi omnidirectional (NOT high-gain — too directional
  vertically for hilly terrain)
- **Backhaul:** 4G cellular (good EE/Vodafone coverage on Mendip tops)
  or farmhouse Ethernet
- **Enclosure:** IP67 rated or Conduit in IP65 Hammond box

#### Option 2: Two Gateways — Escarpment Top + Valley Floor
- Gateway 1: Escarpment top (~250 m) — covers plateau + upper scarp
- Gateway 2: Blagdon village / lakeside (~80 m) — covers valley sensors
- TTN/Loriot packet deduplication gives free redundancy
- Both gateways on same TTN application or Loriot account

#### Option 3: Single Gateway + LoRa Relay
- Relay at mid-scarp (~150–180 m) — "The Rocks" viewpoint area
  (51.3242°N, 2.7214°W, BS40 7TR)
- Cheaper than second gateway but adds complexity
- RAK7268 (~£80) better value than a relay node

### Gateway Hardware: Multitech Conduit
- Use **LoRa Basics Station** protocol (mPower firmware 5.30+)
- Outdoor deployment: use **IP67 Conduit variant** or mount indoor
  Conduit in Hammond IP65 enclosure with external N-type antenna
- **4G-LTE cellular backhaul** option — no trenching required for
  hilltop installation
- Antenna: **3 dBi omni** — avoid >5 dBi in hilly terrain

### Network Server: TTN vs Loriot

| Factor | TTN | Loriot |
|---|---|---|
| Cost | Free | ~€0.10/device/month |
| Fair use limit | 30 uplinks/day/device | None |
| Data ownership | Shared | Private tenant |
| MultiTech Conduit support | ✅ Excellent | ✅ Excellent |
| Recommendation | Prototype/validate | Long-term deployment |

### Seasonal Canopy Warning

| Season | Canopy | LoRa impact |
|---|---|---|
| Winter (Nov–Feb) | Bare | SF7–SF9 adequate |
| Spring (Apr–May) | Partial | SF10 may be needed |
| Summer (Jun–Sep) | Full | Up to 30 dB extra loss — use SF12 |
| Autumn (Oct–Nov) | Declining | Improving |

### Site Validation Tools
- **heywhatsthat.com** — viewshed analysis from candidate gateway points
- **Radio Mobile Online** / **CloudRF** — RF propagation modelling
- **OS Explorer Map 141** (Cheddar Gorge & Mendip Hills West)
- **Walk-test** with transmitting node + TTN Mapper / RSSI logger

---

## 4. Computing Hardware

### Raspberry Pi 5

| Spec | Value |
|---|---|
| CPU | Cortex-A76, 2.4 GHz quad-core |
| RAM | 4 GB / 8 GB |
| OS | Raspberry Pi OS (Debian) |
| acoupi compatible | ✅ Fully supported |
| Idle power (headless) | ~2.7–3.2 W |
| Active power (inference) | ~5–8 W |
| Typical average | ~4–5 W |
| Cost | ~£80 |

#### RPi5 Power Optimisation
```bash
# /boot/firmware/config.txt
dtoverlay=disable-wifi        # save ~0.3W if using cellular
dtoverlay=disable-bt          # save Bluetooth draw
hdmi_blanking=2               # disable HDMI completely

# CPU governor
echo powersave | sudo tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor
```
Total savings: ~1–2 W reduction achievable

---

### Arduino UNO Q (Qualcomm acquisition, late 2025)

#### Architecture: Dual Brain
| Component | Chip | Role |
|---|---|---|
| MPU (Linux side) | Qualcomm Dragonwing QRB2210 | Debian Linux, Python, ML inference |
| MCU (real-time side) | STMicro STM32U585 (Cortex-M33, 160 MHz) | Arduino sketches, Zephyr OS, GPIO |
| Radio | WCBN3536A | Wi-Fi 5 + Bluetooth 5.1 |

#### QRB2210 Features Relevant to Bioacoustics
- 4× Cortex-A53 @ 2.0 GHz
- **Dedicated DSP** — audio feature extraction, keyword spotting
- Adreno 702 GPU — ML acceleration
- Runs full Debian Linux — **acoupi installs natively**

#### Variants

| Variant | RAM | Storage | Price |
|---|---|---|---|
| UNO Q 2GB | 2 GB LPDDR4x | 16 GB eMMC | ~$44 (~£35) |
| UNO Q 4GB | 4 GB LPDDR4x | 32 GB eMMC | ~$55 (~£44) |

#### Power
| Mode | Power |
|---|---|
| Typical (claimed) | ~1 W |
| Real-world measured | ~1.5–2.5 W |
| Input options | USB-C PD (5V/3A) OR **VIN pin 7–24V direct** |

> ⭐ **VIN pin accepts 12V directly** — connect straight to Victron load output,
> no buck converter needed for the UNO Q itself

#### GPIO
- 22× digital GPIO — **3.3V logic** (also 5V tolerant via IOREF)
- I2C: D20/D21 + Qwiic connector
- UART: D0/D1
- SPI: D10–D13
- CAN Bus: D4/D5

#### Cost Comparison for 12 Nodes
| Platform | Unit cost | 12-node total |
|---|---|---|
| RPi5 4GB | ~£80 | ~£960 |
| UNO Q 2GB | ~£35 | ~£420 |
| **Saving** | | **~£540** |

---

## 5. Power System

### Hardware
| Component | Spec |
|---|---|
| Battery | 30Ah, 12V = 360 Wh (288 Wh usable at 80% DoD) |
| Solar panel | 50W peak |
| Charge controller | **Victron MPPT 75/10** |
| Victron load output | **15A continuous at 12V** (180W available) |
| Victron BatteryLife | Auto-disconnect on low battery ✅ |
| Low voltage disconnect | 11.1V or 11.8V (configurable) |

### Solar Yield (Mendip Hills)
| Season | Daily yield |
|---|---|
| Summer | ~125–175 Wh/day |
| Winter | ~50–75 Wh/day |

### Power Budget per Node

| Configuration | Daily consumption | Summer balance | Winter balance |
|---|---|---|---|
| RPi5 + 4G modem (~5.5W avg) | ~132 Wh/day | ✅ +ve | ❌ −ve |
| RPi5 + Notecard (~4W avg) | ~96 Wh/day | ✅ +ve | ⚠️ Marginal |
| **UNO Q + 4G modem (~3W avg)** | **~72 Wh/day** | **✅ +ve** | **✅ +ve** |
| **UNO Q + Notecard (~2W avg)** | **~48 Wh/day** | **✅ +ve** | **✅ +ve** |

### Battery Autonomy (No Sun)
| Configuration | Days of autonomy |
|---|---|
| RPi5 + modem | ~2.2 days |
| UNO Q + 4G modem | ~4 days |
| **UNO Q + Notecard** | **~6 days** |

---

## 6. Power Chain Architecture

### Current Setup (as purchased/tested)
```
[50W Solar Panel]
      ↓
[Victron MPPT 75/10]
      ↓ 12V load output (15A max)
[MZHOU M32J5V3A1SC Buck Converter — 12V→5V, 3A/15W MAX, IP67]
      ↓ 5V
[Anker A8357 USB-C Hub 5-in-1]
  - PD pass-through → UNO Q USB-C
  - USB-A ports → bus-powered from UNO Q OTG (problematic)
```

### Issues Identified
| Component | Issue |
|---|---|
| MZHOU buck (15W) | Thermal stress at load inside sealed enclosure; 40°C rise at 11W measured |
| Anker A8357 hub | Bus-powered only — USB-A ports draw from UNO Q OTG FET, not external supply |
| E3372h via Anker hub | Dongle's 700mA peak routes back through UNO Q — risk of instability |

### Recommended Architecture
```
[Victron MPPT 75/10 — 12V load output]
    ├──→ [UNO Q VIN pin — 12V direct] (UNO Q onboard LMR51440 buck handles conversion)
    └──→ [TP-Link UH700 — 12V/2.5A barrel jack]
               ↓ USB upstream cable (data only — no power drawn back)
          [UNO Q USB-C]
               ↓ USB-A port (hub's own 12V-powered supply)
          [Cellular modem — fully powered by hub, independent of UNO Q]
```

### Hardware Purchased / In Hand
| Item | Status | Notes |
|---|---|---|
| Victron MPPT 75/10 | ✅ Installed | |
| 30Ah 12V battery | ✅ Installed | |
| 50W solar panel | ✅ Installed | |
| MZHOU 12V→5V 3A buck | ✅ Purchased | Repurpose or keep as spare |
| Anker A8357 5-in-1 hub | ✅ Purchased | Replace with TP-Link UH700 |
| **TP-Link UH700** | ✅ **Purchased** | 12V/2.5A barrel jack, 7-port USB 3.0 |
| E3372h dongle | ✅ In drawer | Test device — use for initial validation |
| UNO Q | ✅ (assumed) | Primary compute platform |

### TP-Link UH700 Connection to Victron
```
Victron load output (+12V / GND terminals)
    ↓
DC barrel pigtail cable (5.5mm × 2.1mm, centre positive, ~£2)
    ↓
TP-Link UH700 barrel jack input
```

### Watchdog: Modem Recovery Without Remote Access
Since the network goes down when the modem hangs, software-based
remote power-cycling is impossible. Use the UNO Q's dual-brain
architecture instead:

```
QRB2210 Linux → heartbeat GPIO → STM32U585 MCU
                                      ↓ (if heartbeat lost >5 min)
                                 GPIO → N-channel MOSFET gate (IRLZ44N ~£0.50)
                                      ↓
                                 Cuts 5V to modem for 10 seconds
                                      ↓
                                 Modem reboots → LTE re-registers → network recovers
```
Cost: ~£0.50 MOSFET. No hub per-port switching needed.

---

## 7. Cellular Connectivity

### UK Network Status (June 2026) — CRITICAL

| Technology | Status | Notes |
|---|---|---|
| **2G GSM** | 🔴 **DEAD** | Vodafone off Jan 2024, Three off Aug 2024, EE/O2 imminently off |
| **3G UMTS** | 🔴 **DEAD** | All 4 operators switched off by Feb 2025 |
| **4G LTE Cat-1/Cat-4** | ✅ Active | All 4 operators — primary technology |
| **LTE-M (Cat-M1)** | ✅ Active | EE + Vodafone — good rural coverage |
| **NB-IoT** | ✅ Active | EE + Vodafone — best rural penetration |
| **5G** | ✅ Urban only | Not relevant for Mendips |

> ⚠️ Any modem listing "2G fallback" — this is irrelevant for UK deployment.
> Treat all current modems as LTE-only for UK use.

### Key LTE Bands for Rural Mendip Somerset

| Band | Frequency | Operators | Importance |
|---|---|---|---|
| **Band 20** | **800 MHz** | EE + Vodafone | ✅ **CRITICAL — primary rural band** |
| Band 8 | 900 MHz | O2 | ✅ Good rural penetration |
| Band 1 | 2100 MHz | All | Urban/suburban |
| Band 3 | 1800 MHz | All | Urban/suburban |

**Band 20 (800 MHz) support is mandatory for your Mendip deployment.**

### IoT SIM Recommendations

#### 🥇 1NCE — RECOMMENDED for Fixed Deployments
| Feature | Detail |
|---|---|
| Price | **€12 one-time per SIM** (raised from €10 Jan 2026) |
| Data | **500 MB over 10 years** |
| Top-up | €10 per additional 500 MB |
| Technologies | 2G/3G/4G/LTE-M/NB-IoT |
| Direct MQTT | ✅ Standard internet APN — `iot.1nce.net` |
| Industrial SIM | +€1 (ruggedised, extended temp range) |
| Min order | 1 SIM |

**12-node total cost:**
```
12 × €12 = €144 (~£122) — ONE TIME, for 10 years
Industrial upgrade: 12 × €2 = €24 extra
Total: ~€168 (~£143) for a decade of connectivity
```

**Data usage estimate:**
- ~10–40 MB/node/year at typical detection rates
- 500 MB per SIM lasts **12–50 years** — top-up essentially never needed

#### 🥈 KeySIM — Best for Multi-Network Coverage Resilience
| Plan | Monthly cost | Networks | Data rate |
|---|---|---|---|
| NB-IoT/Cat-M | £0.95/SIM | Vodafone + O2 | £0.005/MB |
| Standard (10–99 SIMs) | £3.00/SIM | EE+Voda+O2+Three | £0.003/MB |

**12-node annual cost:**
- NB-IoT plan: ~£137/year (all 12 nodes)
- Standard 4G plan: ~£432/year (all 12 nodes)

**Advantages:**
- All 4 UK networks on one SIM — unsteered roaming
- Free trial SIM available — test Mendip coverage before committing
- KeySecure private APN/VPN option available

#### Verdict: Test both
> Order one 1NCE SIM and one KeySIM trial SIM. Test at worst-case
> sensor locations (deepest woodland, valley floor). 1NCE wins on
> cost if coverage is adequate. KeySIM wins on resilience if coverage
> is marginal at some sites.

---

## 8. Cellular Modems for UNO Q

### E3372h Dongle — Test Device (Already Owned)

| Spec | Detail |
|---|---|
| Technology | LTE Cat-4 |
| Mode | HiLink — appears as USB Ethernet (eth1/usb0) |
| Idle power (connected) | ~150–200 mA (~0.75–1.0 W) |
| Peak TX | ~700 mA (~3.5 W) |
| Antenna | CRC9 external ports (h variants) — **external antenna essential** |
| Antenna adapter | CRC9 → SMA pigtail (~£3–5) |
| Linux setup | Plug and play — NetworkManager handles automatically |

**Variants — always buy `h` variants for external antenna:**
- E3372h-153, E3372h-320, E3372h-607 ✅
- E3372s-xxx ❌ (internal antenna only — avoid)

**Linux quick setup:**
```bash
ip link show                    # see new usb0/eth1 interface
nmcli device status             # should show connected
ping -I u[IPAddress1].8.8            # test connectivity
# acoupi needs no changes — sees cellular as normal network
```

**Antenna routing for sealed enclosure:**
```
E3372h → CRC9 × 2 → CRC9-to-SMA pigtail (30cm RG174)
       → SMA female bulkhead through enclosure wall (IP68 gland)
       → short LMR-200 run
       → 5 dBi omni antenna mounted ABOVE canopy on sensor pole
```

---

### Coolwell A7670E LTE Cat-1 HAT — Best Available Now

**Amazon UK: [B097K14K18](https://www.amazon.co.uk/dp/B097K14K18)**

| Spec | Detail |
|---|---|
| Module | SIMCom A7670E |
| Technology | LTE Cat-1 — **no 2G/3G dependency** |
| UK bands | LTE-FDD incl. **Band 20 (800 MHz)** ✅ |
| Rating | ⭐ 4.6/5 (12 reviews) |
| Interface | USB-C AND UART (3.3V/5V jumper selectable) |
| Idle current | ~1–5 mA (significantly better than E3372h) |
| Peak TX | ~1.5–2 A — **must be externally powered, not from UNO Q** |
| Antenna | SMA + antenna included in box |
| Delivery | Available now on Amazon UK |
| Price | ~£35–50 |

**Real-world review match:** German verified purchaser used identical
setup — solar + battery + LTE multi-SIM + waterproof enclosure +
wildlife monitoring. *"Does its job flawlessly."*

**Connection via USB (recommended):**
```
Coolwell HAT USB-C → TP-Link UH700 USB-A port (hub powers HAT)
TP-Link UH700 upstream → UNO Q USB-C (data)
Linux: /dev/ttyUSB0 → ModemManager → NetworkManager → usb0 interface
acoupi MQTT → your broker (no code changes)
```

**Linux setup:**
```bash
sudo apt install modemmanager network-manager
mmcli -L                                          # lists A7670E modem
nmcli con add type gsm ifname ttyUSB0 apn iot.1nce.net  # 1NCE APN
```

---

### Blues Notecard Cellular — Lowest Power Option

**Available from DigiKey UK: NOTE-WBEX-500**
- 17 units in stock at DigiKey UK — £58.93 ex VAT each
- Lead time for restock: 8 weeks — buy from current stock

| Spec | Detail |
|---|---|
| Technology | LTE Cat-1, EMEA bands |
| **Idle current** | **< 8 µA — essentially zero** |
| Active TX | ~250–350 mA |
| Data included | **500 MB / 10 years prepaid — no SIM required** |
| Interface | I2C (3.3V native) or UART |
| GNSS | Integrated GPS |
| Notecarrier A | $25 from shop.blues.com (has onboard antenna, solar JST, LiPo JST) |

> ⚠️ **Narrowband (NB-IoT/LTE-M) Notecard discontinued for <250 unit orders.**
> NOTE-WBEX-500 (LTE Cat-1) is the current single-unit product.
> Idle power is identical to the old Narrowband variant (<8 µA).

**Notecarrier A — Key Features for Your Deployment:**
- Built-in LTE + GPS antennas (no antenna routing needed)
- Solar JST input (3.94–7.18V) — can power independently
- LiPo JST + charging circuit — small buffer battery option
- Dual Qwiic I2C connectors — direct to UNO Q I2C pins

**Wiring to UNO Q (4 wires):**
```
Notecarrier A SDA → UNO Q D20
Notecarrier A SCL → UNO Q D21
Notecarrier A GND → UNO Q GND
Notecarrier A V+  → UNO Q 3.3V
```

**acoupi Python integration:**
```python
import notecard
from periphery import I2C

port = I2C("/dev/i2c-1")
card = notecard.OpenI2C(port, 0, 0)

# Configure Notehub routing to your MQTT broker
req = {"req": "hub.set",
       "product": "com.yourorg.wampam",
       "mode": "periodic",
       "outbound": 60}
notecard.Transaction(card, req)

# Send detection event
def send_detection(species, confidence, timestamp):
    req = {"req": "note.add",
           "file": "detections.qo",
           "body": {
               "species": species,
               "confidence": round(confidence, 3),
               "ts": timestamp
           }}
    notecard.Transaction(card, req)
```

**Notehub event credits (ongoing cost awareness):**
- Free tier: 5,000 events/month
- Recommendation: batch detections into hourly summaries to stay
  within free tier — reduces events by 50–200× with minimal
  research data loss

---

## 9. Modem Comparison Summary

| Modem | Technology | Idle power | Peak power | Antenna | Cost | Availability | acoupi path |
|---|---|---|---|---|---|---|---|
| **E3372h dongle** (owned) | LTE Cat-4 | ~1.0 W | ~3.5 W | CRC9 external | £0 | ✅ Now | USB network interface |
| **Coolwell A7670E HAT** | LTE Cat-1 | ~0.025 W | ~7.5 W | SMA included | ~£40 | ✅ Now | USB /dev/ttyUSB0 |
| **Blues Notecard WBEX** | LTE Cat-1 | **~0.00004 W** | ~1.75 W | Onboard (Notecarrier A) | ~£79 | ✅ DigiKey UK | I2C → Notehub → MQTT |

---

## 10. Recommended Test Sequence (Starting Monday)

### Phase 1 — Immediate (Week 1): Validate with Existing Hardware
```
Hardware: UNO Q + E3372h dongle + TP-Link UH700 + Victron system
SIMs: Order 1× 1NCE SIM (€12) + 1× KeySIM trial SIM (free)

Power chain:
  Victron 12V → UNO Q VIN pin (direct)
  Victron 12V → TP-Link UH700 barrel jack (via DC pigtail ~£2)
  TP-Link USB-A → E3372h dongle
  TP-Link upstream → UNO Q USB-C (data)

Test:
  1. Confirm LTE connection with 1NCE SIM (APN: iot.1nce.net)
  2. Confirm LTE connection with KeySIM trial SIM
  3. acoupi MQTT publish to your existing broker
  4. Measure actual current draw at 12V input (clamp meter)
  5. Walk-test: carry node to each of 12 sensor positions,
     verify MQTT connectivity at each location
```

### Phase 2 (Week 2–3): Lower Power Modem Validation
```
Hardware: Add Coolwell A7670E HAT (order from Amazon UK B097K14K18)

Test:
  1. Connect via USB to TP-Link hub
  2. Compare idle current draw vs E3372h (should be ~40× lower)
  3. Validate MQTT connectivity with same 1NCE/KeySIM SIMs
  4. Validate Band 20 connection at worst-case Mendip sensor sites
```

### Phase 3 (Week 3–4): LoRaWAN Gateway Validation (Parallel)
```
Hardware: Multitech Conduit Gateway
Network: TTN (existing account)

Test:
  1. Mount Conduit at escarpment top candidate location
     (Ubley/Compton Martin area ~51.297°N, 2.710°W)
  2. Walk-test all 12 sensor positions with LoRa node
  3. Use heywhatsthat.com viewshed to pre-validate LoS
  4. Record RSSI + SNR at each position
  5. Compare summer vs winter canopy impact
     (if deploying across seasons)
```

### Phase 4: Deployment Decision
Based on Phase 1–3 results, choose per-node connectivity:
- **Good cellular coverage at all 12 sites:** → Cellular only
  (UNO Q + A7670E + 1NCE)
- **Some sites cellular dead spots:** → Hybrid (cellular where possible,
  LoRaWAN where not)
- **Most sites cellular dead spots:** → LoRaWAN primary, cellular
  for gateway backhaul only

---

## 11. Key Contacts and Resources

### Tools
- **heywhatsthat.com** — free viewshed/LoS analysis
- **Radio Mobile Online** — RF propagation modelling with terrain
- **TTN Mapper** — walk-test RSSI logging for LoRaWAN
- **Ofcom coverage checker** — UK operator coverage maps
- **Opensignal** — real-world signal strength at specific locations
- **OS Explorer Map 141** — Cheddar Gorge & Mendip Hills West

### Suppliers
| Item | Supplier | Link / Notes |
|---|---|---|
| Blues Notecard WBEX-500 | DigiKey UK | 17 in stock, £58.93 ex VAT |
| Blues Notecarrier A | shop.blues.com | $25 + shipping to UK |
| Coolwell A7670E HAT | Amazon UK | B097K14K18 |
| 1NCE IoT SIM | 1nce.com | €12/SIM, 500 MB/10yr |
| KeySIM trial SIM | keysim.co.uk | Free trial available |
| DC barrel pigtail (5.5×2.1mm) | Amazon UK | ~£2 — Victron to TP-Link hub |
| IRLZ44N MOSFET (watchdog) | Amazon UK / RS Components | ~£0.50 — modem watchdog circuit |
| CRC9→SMA pigtail (E3372h antenna) | Amazon UK | ~£3–5 per dongle |
| 5 dBi outdoor LTE antenna | Amazon UK | For E3372h / external antenna option |
| IP68 cable gland (SMA bulkhead) | Amazon UK / RS Components | For routing antenna through enclosure |

### Key References
- acoupi framework paper: *Methods in Ecology and Evolution*, 2026
- Arduino UNO Q official store: store.arduino.cc/pages/uno-q
- Victron MPPT 75/10 datasheet: victronenergy.com
- Blues Notecard docs: dev.blues.com
- 1NCE platform docs: 1nce.com/developer-hub

---

## 12. Decision Matrix Summary

| Factor | LoRaWAN | Cellular (1NCE + A7670E) | Cellular (Notecard) |
|---|---|---|---|
| Hardware cost (12 nodes) | Gateway ~£200 + nodes | ~£480 (modems) + £122 (SIMs) | ~£708 (cards) + £240 (carriers) |
| Ongoing cost/year | £0 (TTN) | ~£0 (1NCE data lasts decades) | ~£0 (data included) |
| Coverage risk | ⚠️ Terrain/canopy dependent | ✅ EE Band 20 rural coverage | ✅ Same |
| Data ownership | TTN shared | ✅ Your own MQTT broker | Notehub + your broker |
| Power per node | LoRa node ~0.1W TX | ~3W avg (A7670E) | ~2W avg (Notecard) |
| Setup complexity | Medium (gateway config) | Low (plug and play) | Low (I2C + 4 wires) |
| Redundancy | ⚠️ Single gateway risk | ✅ Cellular independent per node | ✅ Same |
| Best for | Dense multi-node, low power | Direct, simple, cheap OPEX | Lowest power, no SIM admin |

---

*End of report — Version 1.0, June 2026*
*Next review: After Phase 1 and Phase 2 testing (target: July 2026)*