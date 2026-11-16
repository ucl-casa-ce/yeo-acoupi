# acoupi messaging modes

How detections become MQTT messages, and the three ways to shape them. Only the
first is currently wired up.

## How messages get made

Two independent mechanisms feed one outbox:

```
recording -> model -> message_factories  ─┐
                                          ├─> SqliteMessageStore -> send_messages -> Messenger -> MQTT
             (schedule) -> summarisers   ─┘
```

- **`message_factories`** hang off the detection task. They fire once per
  recording, immediately after inference.
- **`summarisers`** hang off `generate_summaries`, which runs on its own
  schedule and reads back over the detection store.
- **`send_messages`** drains the store on a third schedule. Nothing is lost if a
  send fails — messages stay queued until one succeeds.

The three schedules are independent, which is what lets you detect every 30 s
but transmit once a day.

---

## Mode A — one message per recording

**Status: built, and what's running now.**

```python
detect_birds = tasks.generate_detection_task(
    store=self.store,
    model=self.bird_model,
    message_factories=[
        DetectionThresholdMessageBuilder(
            detection_threshold=config.birdnet.detection_threshold
        )
    ],
    message_store=self.message_store,
)
```

Emits one message per recording containing every detection above the threshold.
Note it's per *recording*, not per detection — a 3 s clip with four species is
one message carrying four.

Watch the payload. `DetectionThresholdMessageBuilder` serialises the **full model
output**: model name, recording file path, deployment block, and a bounding box
per detection. About 250 bytes per detection, against 7 bytes packed. Fine for
testing over WiFi, wasteful over cellular.

Without a message factory, **no detection messages are created at all** — this
was the original state of the program, where only the two hard-coded species
summaries ever transmitted.

**Use for:** bench testing, when you want to see each detection arrive.

---

## Mode B — batched individual detections

**Status: not built. Needs a custom summariser, roughly 20 lines.**

One message per hour (or per day) that lists each detection individually —
species, confidence, timestamp — rather than one message per recording.

Write it as a `Summariser`, not a batching Messenger. acoupi's `Messenger`
contract is `send_message(message) -> Response`, one at a time, and each message
is marked sent based on the response it returns; buffering inside the messenger
means you cannot honestly report success. A summariser reads the store on a
schedule and emits one message, which fits the existing machinery exactly.

Shape it on `DetectionCountByTagSummary` in `components.py` — same
`build_summary(now)` signature, but enumerate instead of counting:

```python
detections = self.store.get_detections(after=self._last_summary)
# -> [{"s": species, "c": confidence, "t": timestamp}, ...]
```

Two things to get right:

- **Filter by model.** `get_detections()` returns everything, so a bird
  summariser will pick up bat detections unless you filter by model name or tag,
  the way `DetectionCountByTagSummary` filters on `key`/`value`.
- **Watch payload size.** A busy dawn hour could be a few hundred detections in
  one publish. The A7670E's maximum `CMQTTPAYLOAD` size is unverified — chunk at
  a configurable byte limit so a big hour becomes 2–3 publishes rather than
  failing.

**Use for:** birds in production. Low enough volume to keep individual records.

---

## Mode C — summarised counts

**Status: components exist in acoupi; only needs wiring.**

Aggregates a window into per-species figures. Three options, all `Summariser`s:

| Component | Emits |
|---|---|
| `DetectionCountByTagSummary` (yours, in `components.py`) | Count for one named species above a score |
| `StatisticsDetectionsSummariser` (acoupi) | Per species: count, mean, min, max confidence |
| `ThresholdsDetectionsSummariser` (acoupi) | Per species: counts and means bucketed low/mid/high confidence |

`generate_summaries` is already scheduled hourly with two hard-coded species
(*Pipistrellus pipistrellus*, *Troglodytes troglodytes*). Swapping in
`StatisticsDetectionsSummariser` covers all species without naming them.

For bats, prefer `ThresholdsDetectionsSummariser` over `Statistics` — a single
mean across 250 calls in an hour is a weak statistic; confidence bands say more.

**Use for:** bats, always. A dense night is ~3,600 detections/hour. Sent
individually that is ~345 kB/night of payload, and at 1–2 s per AT publish those
publishes physically cannot fit in the night that produced them.

---

## Recommended production shape

- **Bats → Mode C.** Non-negotiable on both data volume and airtime.
- **Birds → Mode B.** Individual records, batched into one message.
- **Drop the Mode A message factories** — they'd duplicate everything the
  summarisers already send.

### Switching over

1. Remove `message_factories=[...]` from both detection tasks in `program.py`.
2. Add the bat summariser and the new bird summariser to `generate_summaries`.
3. Set `send_messages` to your chosen cadence — currently 15 s for testing.
4. **Order the two schedules.** `generate_summaries` runs on `crontab(hour="*",
   minute=0)`. If `send_messages` also fires at :00 it may run before the batch
   exists and ship an hour late. Offset it — summarise at :00, send at :05.
5. Drop the heartbeat from 30 s to hourly. The line is already in `program.py`,
   commented out.

### Cadence

Sending is what costs, not payload — roughly 92% of hourly traffic is connection
overhead. See `5-data-budget/`. Hourly is affordable on any of the tariffs
considered; a single daily push saves ~157 MB per device over ten years and cuts
radio-on time from ~109 to ~4.6 hours a year, which matters more for the solar
budget than for the bill.
