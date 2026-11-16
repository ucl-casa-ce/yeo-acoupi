# Running acoupi on the Arduino UNO Q

Notes from getting acoupi 0.5.2 running on an UNO Q — Debian 13 (trixie),
aarch64, Qualcomm QCM2290. These are the adaptations this hardware/OS
combination needs; follow them in order and you should end up where we got to.

Verified on `unoq-yeo-5`. Identify boards by ADB serial, not hostname — the
hostnames on these units have been renamed more than once.

---

## 1. System packages

`pyaudio` has no aarch64 wheel, so it compiles during install. That needs a
toolchain, not just the portaudio headers — a stock board has neither.

```bash
sudo apt install -y portaudio19-dev build-essential pkg-config
curl -LsSf https://astral.sh/uv/install.sh | sh   # uv isn't in Debian's archive
export PATH="$HOME/.local/bin:$PATH"
```

The board ships Python 3.13 and the project pins 3.12 — uv fetches its own, so
nothing to do there.

Disk: `/home/arduino` is its own 18 GB partition. `df -h /home` misleadingly
reports the smaller root partition; use `df -h /home/arduino`.

---

## 2. Keep torch on CPU

On linux-aarch64, PyPI's torch wheels carry the CUDA stack for server-class ARM
(GH200/sbsa). A plain `uv sync` therefore pulls ~2.3 GB of NVIDIA libraries onto
a board with no GPU. Add to `pyproject.toml`:

```toml
dependencies = [
  ...
  # Declared directly so [tool.uv.sources] applies — see note below.
  "torch==2.11.0",
  "torchaudio==2.11.0",
]

[[tool.uv.index]]
name = "pytorch-cpu"
url = "https://download.pytorch.org/whl/cpu"
explicit = true

[tool.uv.sources]
torch = { index = "pytorch-cpu" }
torchaudio = { index = "pytorch-cpu" }
```

Two things worth knowing:

- **`[tool.uv.sources]` only applies to direct dependencies.** torch arrives
  transitively via batdetect2, so the index + sources block on its own does
  nothing and uv re-resolves the full CUDA stack with no warning. Declaring
  torch and torchaudio in `[project.dependencies]` is what makes the pin bite.
- **Use `==`, not `>=`.** With `>=` torch floated to 2.13.0 while torchaudio
  stayed at 2.11.0.

Result: 193 → 176 packages, torch download 400 MB → 141 MB.

If your dev box has a GPU you'll want this scoped to `platform_machine ==
'aarch64'` rather than applied globally.

```bash
uv lock
UV_CONCURRENT_DOWNLOADS=4 UV_HTTP_TIMEOUT=180 uv sync
```

Throttling matters — ~50 concurrent wheel downloads saturate the board's WiFi
and time out. Also: the board needs its own internet. ADB-over-USB is a debug
channel, not a network route.

---

## 3. Recording: PipeWire 1.4.2

acoupi's `PWRecorder` calls `pw-record --sample-count=N`. That flag arrives in
PipeWire 1.5; Debian 13 ships 1.4.2 and the UNO Q runs Qualcomm's
`1.4.2-1~qcom1` overlay build. Upgrading would displace the vendor audio build
for one flag, and there's no 1.6 for trixie anyway, so bound the capture with
`timeout` instead — the only other way to stop `pw-record`. Use SIGINT so it
finalises the WAV header.

One wrinkle: `timeout` counts wall clock from exec while `pw-record` takes
~120 ms to start streaming, so a bare 3 s request yields ~2.88 s. Over-record by
a margin and trim back to exact length. The trim isn't cosmetic —
`BaseAudioRecorder.check()` records 0.1 s and asserts the duration within
±0.01 s, and BirdNET analyses a fixed 3.0 s window.

→ `src/acoupi_yeo_valley/recorder.py`, class `PWRecorderCompat`. Point
`program.py`'s two recorders at it.

---

## 4. Device setup: skip enumeration

`PWRecorderConfig.setup()` calls `get_input_devices()`, which reads `pw-dump`
and expects the `EnumFormat` `rate` and `channels` values to be scalars.
Multi-rate USB mics — the AudioMoth included — report them as range/choice
objects, so the set comprehension raises `unhashable type: 'dict'` and setup
stops.

Enumeration only exists to populate the interactive device picker, and you
already know the node name, so subclass the config and take it directly.

→ `PWRecorderCompatConfig` in the same file; point `config.py`'s two recorder
fields at it.

---

## 5. Config field types

acoupi's config parser has handlers for `str`, `int`, `float`, `bool` and nested
models. Two fields in our own models used types outside that set, and setup
aborted with `NotImplementedError` when it reached them:

- `BirdNETConfig.species_list_file` was `Path | None` → changed to `str`
  (empty = unset), wrapped in `Path()` where it's used.
- `BatRecordingConfig.start_recording` / `end_recording` are `datetime.time` →
  left as-is, but set through defaults rather than the setup prompts. Answer
  **n** to "Would you like to set bats?" during setup.

---

## 6. File cleanup when both recorders share a samplerate

`program.py`'s `has_been_processed` decides whether a recording can be deleted.
It used to key off samplerate: >90 kHz required the bat model to have run,
<90 kHz the bird model. That assumes the two recorders use different rates.

With both at 192 kHz, bird recordings are >90 kHz but only ever see the bird
model, so neither branch matched, they were never marked processed, and audio
accumulated in the shm audio dir until the board fell over.

Models are chosen by the recording task's callback, not by samplerate, so the
test is simply whether a model ran:

```python
model_names = {output.name_model for output in outputs}
return bool(model_names & {self.bat_model.name, self.bird_model.name})
```

Works for matching or mixed samplerates, so no code change if you later drop
birds to 48 kHz.

---

## 7. Setup and verify

```bash
env -u CUDA_VISIBLE_DEVICES uv run pytest tests/ -q     # expect 13 passed
env -u CUDA_VISIBLE_DEVICES uv run acoupi setup --program acoupi_yeo_valley.program
env -u CUDA_VISIBLE_DEVICES uv run acoupi config get
env -u CUDA_VISIBLE_DEVICES uv run acoupi check
env -u CUDA_VISIBLE_DEVICES uv run acoupi deployment start
```

`--program` takes the **module** path (`acoupi_yeo_valley.program`), not
`module.Class`. Commands are under `acoupi deployment` (`start`/`stop`/`status`).

`acoupi check` test-records 0.1 s on each recorder and pings the broker, so it
catches a bad device name or bad credentials before you're chasing silence.

Timings measured on-board, useful for sizing:

| Model | Cold (load+infer) | Warm inference |
|---|---|---|
| BatDetect2 | 39 s | ~3.4 s |
| BirdNET | 20 s | ~3.1 s |

Models must stay resident in a long-lived celery worker — a per-cycle restart
would spend all its time loading.

---

## Files changed

| File | Change |
|---|---|
| `pyproject.toml` | CPU torch index + direct torch/torchaudio pins |
| `src/acoupi_yeo_valley/recorder.py` | **New** — `PWRecorderCompat`, `PWRecorderCompatConfig` |
| `src/acoupi_yeo_valley/program.py` | Use `PWRecorderCompat`; rewrite `has_been_processed`; message factories; send interval |
| `src/acoupi_yeo_valley/config.py` | Use `PWRecorderCompatConfig`; bird duration 10 s → 3 s; bat window |
| `src/acoupi_yeo_valley/models.py` | `species_list_file` → `str` |

`.orig` backups of each sit alongside them on the board.

---

## Still open

- No detection has yet reached the broker end to end. Recording is proven;
  detection → message → broker is not.
- The amended source is in `code/` alongside this doc. It was never committed to
  git, so treat this pack as the copy of record. Run `uv sync` then
  `pytest tests/ -q` (expect 13 passed) before relying on it.
- Test-only settings to revert for production: all-day bat window, 15 s send
  interval, per-detection messages, 30 s heartbeat.
