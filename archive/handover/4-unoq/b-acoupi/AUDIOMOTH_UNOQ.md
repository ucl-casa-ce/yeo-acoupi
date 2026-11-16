# AudioMoth USB mic on the UNO Q

Getting the AudioMoth recognised, verified and wired into acoupi. Assumes the
acoupi install is already done — see `ACOUPI_UNOQ_DEPLOY.md`, particularly §3
and §4, which are the recording-related adaptations this mic depends on.

---

## 1. Plug it in

The AudioMoth goes on a USB dongle/hub. The board's own USB-C port is taken by
ADB when you're developing over cable, and in the field the dongle also carries
the modem and SD card. If you're working over cable, plan on SSH over WiFi
instead so the port is free.

---

## 2. Find the node name

```bash
wpctl status                              # look under Audio -> Sources
pw-cli ls Node | grep -E "node.name|node.description"
```

You want **`node.name`**, not the description. Ours:

```
alsa_input.usb-openacousticdevices.info_192kHz_AudioMoth_USB_Microphone_0192_243B1F055B1F7904-00.mono-fallback
```

The description ("192kHz AudioMoth USB Microphone Mono") won't work as a target.

---

## 3. Test capture before involving acoupi

This is the same command acoupi ends up running, so if it fails here it'll fail
there — with a clearer error.

```bash
NODE="alsa_input.usb-openacousticdevices.info_192kHz_AudioMoth_USB_Microphone_0192_243B1F055B1F7904-00.mono-fallback"
timeout 4 pw-record --rate=192000 --channels=1 --target="$NODE" /tmp/t.wav
ls -lh /tmp/t.wav     # expect ~1.5 MB for 4 s
```

Then confirm the file is actually readable and at the right rate — size alone
doesn't prove the WAV header was finalised:

```bash
cd ~/acoupi-yeo-valley
uv run python -c "import soundfile as sf; f=sf.SoundFile('/tmp/t.wav'); print(f.samplerate, f.channels, len(f))"
```

Expect `192000 1` and roughly 740,000 frames.

---

## 4. Put the node name in the config

```bash
export PATH="$HOME/.local/bin:$PATH"
cd ~/acoupi-yeo-valley
env -u CUDA_VISIBLE_DEVICES uv run acoupi config set --field bird_recorder.device_name "$NODE"
env -u CUDA_VISIBLE_DEVICES uv run acoupi config set --field bat_recorder.device_name "$NODE"
env -u CUDA_VISIBLE_DEVICES uv run acoupi deployment stop
env -u CUDA_VISIBLE_DEVICES uv run acoupi deployment start
```

**Use the full node name, not a friendly label.** We initially set
`device_name` to `unoq5-bird` / `unoq5-bat`. `pw-record` doesn't match those, so
it silently falls back to the *default* source — which happened to be the
AudioMoth, so it looked like it worked. That's luck, not configuration: anything
that changes the default source (a second mic, a reboot, the built-in codec)
would quietly redirect capture. Worth pinning properly before this goes to 20
devices.

Useful side effect while debugging: the built-in codec can't do 192 kHz at all,
so **a valid 192 kHz recording can only have come from the AudioMoth.**

---

## 5. Samplerate

Both recorders are set to 192 kHz. BirdNET resamples to 48 kHz internally, so it
accepts the input without complaint — the open question is acoustic, not
software: whether an ultrasonic mic's sensitivity at 2–8 kHz costs you bird
detection accuracy. The lab test is what settles it.

If bird detection looks weak, drop just the bird recorder:

```bash
env -u CUDA_VISIBLE_DEVICES uv run acoupi config set --field bird_recorder.samplerate 48000
```

Config only — no code change needed, because the file-cleanup guard no longer
keys off samplerate (see `ACOUPI_UNOQ_DEPLOY.md` §6).

---

## 6. Recording length

Capture is deliberately over-run by ~0.4 s and trimmed back, because `pw-record`
takes ~120 ms to start and `timeout` counts from exec. You get exactly what you
ask for: 3.0 s requested → 3.0 s delivered (576,000 frames at 192 kHz), 0.1 s
for the health check. If recordings ever come back *short*, raise
`STARTUP_MARGIN_S` in `recorder.py`.

---

## Quick reference

| | |
|---|---|
| Node name | `alsa_input.usb-openacousticdevices.info_192kHz_AudioMoth_USB_Microphone_0192_243B1F055B1F7904-00.mono-fallback` |
| Samplerate | 192000 (both recorders) |
| Channels | 1 |
| 3 s clip size | ~1.15 MB |
| Recording cadence | bats on :00, birds on :30, one 3 s clip each per 60 s |

Note that last row when testing: it only listens for 3 s in every 60, so play
the test audio continuously or you'll miss the window.
