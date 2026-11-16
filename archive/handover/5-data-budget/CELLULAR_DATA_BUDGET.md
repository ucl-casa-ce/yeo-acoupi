# Cellular data budget

What a detection actually costs to send, and what that means per device over a
10-year deployment. Figures are estimates from protocol sizes, not measured
traffic — worth validating with a test SIM before committing 20 of them.

**Short version: how often you open a connection matters far more than what you
put in it.**

---

## 1. What a detection costs

| Encoding | Per bird detection | Notes |
|---|---|---|
| acoupi default JSON | ~250 B | Full model output — model name, file path, bounding box, nested tags |
| Compact JSON | ~35 B | `{"s":2441,"c":0.82,"t":1752710400}` |
| Packed binary | **7 B** | species `uint16` + confidence `uint8` + timestamp `uint32` |
| Bit-stacked | ~4 B | see §2 |

A bat record is a per-recording aggregate — same fields plus a 1-byte count, so
**8 B** packed.

acoupi's out-of-the-box message format is ~35× larger than the packed form. It
carries a file path and model metadata on every single detection, which you're
paying for on every transmission.

---

## 2. Where bit stacking helps

| Field | Now | Could be | Why |
|---|---|---|---|
| species | 16 bits | **13 bits** | BirdNET 2.4 has ~6,500 classes. Restrict to a UK list (~600) and 10 bits does it |
| confidence | 8 bits | **4 bits** | 0.05 steps across 0.30–1.00 is 14 values. 7 bits if you want 0.01 resolution |
| timestamp | 32 bits | **12 bits** | Send one absolute base per batch, then per-record offset in seconds — an hour fits in 12 bits |
| bat count | 8 bits | 8 bits | Keep it. 255 pings per recording is a realistic ceiling |

Bird record: 13 + 4 + 12 = **29 bits → 4 bytes** padded, plus one 4-byte base
timestamp per batch. That's **7 B → 4 B, about 43% off**.

The timestamp is where the real saving is — 2.5 bytes of the 3. Worth doing if
you ever move to per-detection sends; barely matters if you summarise.

---

## 3. What a transmission costs

Every session pays this regardless of payload:

| Element | ~Bytes |
|---|---|
| DNS lookup | 350 |
| TCP handshake | 180 |
| MQTT CONNECT + CONNACK | 190 |
| PUBLISH headers + PUBACK | 130 |
| DISCONNECT + teardown | 200 |
| **Total** | **~1,050 B** |

Pinning the broker's IP in config removes the DNS lookup — about a third of the
overhead. TLS would add ~2–4 kB per session; we're on plain MQTT (port 1884).

**At hourly sends, roughly 92% of your data is connection overhead, not
detections.**

---

## 4. Usage by batching cadence

Assumes 250 bird detections/day and 50 bat recordings/day (each aggregated),
packed binary. Daily payload = 250 × 7 + 50 × 8 = **2,150 B**.

"Actual" is bytes on the wire. "Billed" applies 1NCE's 1 kB metering per session,
which is what you'll actually be charged — a ~1.14 kB session rounds to 2 kB.

| Cadence | Sessions/day | Actual/day | Billed 10 yr | % of 500 MB | Modem-on/day |
|---|---|---|---|---|---|
| Every detection | 300 | 318 kB | 2,139 MB | 428% | 225 min |
| Every 15 min | 96 | 103 kB | 684 MB | 137% | 72 min |
| Every 30 min | 48 | 53 kB | 342 MB | 68% | 36 min |
| **Hourly** | 24 | 27 kB | **171 MB** | 34% | 18 min |
| Daily push + hourly daytime | 13 | 16 kB | 93 MB | 19% | 10 min |
| 4-hourly | 6 | 8.5 kB | 43 MB | 9% | 4.5 min |
| Twice daily | 2 | 4.3 kB | 21 MB | 4% | 1.5 min |
| **Once daily (08:00)** | 1 | 3.2 kB | **14 MB** | **3%** | 0.8 min |

The payload is identical in every row. The only thing changing is how many times
you open a connection.

### Overnight batching

Hourly sends through the night are the obvious waste: bats detect for ~12 hours
and nothing reads the data until morning. Holding everything and pushing once at
08:00 saves **157 MB per device over 10 years** and takes you from a third of the
allowance to under 3%.

Power is the better argument. Registration dominates the modem's energy cost, so
sessions/day maps almost directly onto radio-on time:

| | Hourly | Once daily |
|---|---|---|
| Radio-on per year | ~109 hours | ~4.6 hours |

Costs of going daily: a 20:00 detection lands 12 hours late, and you won't know a
device has failed until the next morning. Detections themselves are safe —
acoupi's SQLite message store holds them until a send succeeds. If overnight
liveness matters, **twice daily** costs almost nothing over daily (21 MB vs
14 MB) and halves the blind window.

Use `Cellular-Data-Calculator.xlsx` in this folder to try other numbers — it
takes detection counts, record sizes, session overhead and cadence, and is what
produced the table above.

---

## 5. Why bats are always counts

A dense night: ~3,600 bat detections/hour over 12 hours = 43,200 detections.

| Approach | Payload/night |
|---|---|
| Individual detections | 345 kB |
| Count per recording (50/night) | 400 B |
| Count per species per hour | 288 B |

Individual bat detections would burn ~126 MB/year on payload alone — a quarter
of the lifetime allowance, before any connection overhead. There's also a
mechanical limit: each MQTT publish over the AT interface is a full transaction
at ~1–2 s, so 43,200 individual publishes physically cannot fit in the night
they were collected in.

Birds are low-volume enough to send individually. Bats have to be aggregated.

---

## 6. Tariffs

> **What's in the device today: KeySIM (Tele2), APN `key`, roaming Vodafone-UK.**
> That's what the cellular link was proven on. The comparison below is
> forward-looking for the fleet rollout — an open commercial decision, not a
> switch that has already happened.

### Against the 1NCE allowance

1NCE is €12 per SIM for 10 years, 500 MB + 250 SMS, billed at 1 kB granularity
(not the 1 MB-per-session rounding that some consumer tariffs use). Everything
inside the GTP tunnel counts — IP, TCP, MQTT overhead and payload.

Billed figures (1 kB metering) against the 500 MB lifetime allowance:

| Cadence | 10-year usage | Of 500 MB |
|---|---|---|
| Every detection | 2,139 MB | **over budget** |
| Every 15 min | 684 MB | **over budget** |
| Every 30 min | 342 MB | 68% — tight |
| **Hourly** | 171 MB | 34% — workable |
| 4-hourly | 43 MB | 9% |
| **Once daily** | 14 MB | **3%** |

Worth noting how much the 1 kB metering costs you at high session counts: hourly
is 100 MB of actual traffic but 171 MB billed, because each session rounds up.
The finer your cadence, the more you pay for rounding.

Fleet cost: 20 SIMs × €12 = **€240 for 10 years**, each SIM with its own 500 MB
(allowances don't pool). KeySIM's per-MB rate is cheaper but it's ~£3/SIM/month
rental, so ~£7,200 over the same period. 1NCE wins for low-data, long-life,
deploy-and-forget.

**The real risk is coverage, not cost.** 1NCE roams on partner networks and the
site is rural Somerset. Test one SIM on site before buying 20. Also confirm the
UK roaming agreement covers LTE Cat-1 — the A7670E is Cat-1 and we force
LTE-only to avoid 3G retirement.

---

## Takeaway

1. **Pick the cadence first — it sets the bill.** Per-detection and 15-minute
   sends don't fit in 500 MB. Hourly works; once or twice daily is far better on
   both data and power.
2. **Aggregate bats.** Non-negotiable on data and airtime.
3. Move off acoupi's default JSON to something compact — ~35× on payload, though
   it's a small share of the total at slow cadences.
4. Bit stacking is a further ~43%, mostly from batch-relative timestamps. Only
   worth it if you end up on a fast cadence.
5. Pin the broker IP to drop the DNS lookup (~350 B/session).

The one thing to validate before buying 20 SIMs is **coverage on site**, not any
of the above.
