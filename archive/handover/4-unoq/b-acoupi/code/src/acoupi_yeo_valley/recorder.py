"""PipeWire compatibility layer for Debian-stable / Qualcomm PipeWire 1.4.2.

acoupi's PipeWire support assumes a newer PipeWire than Debian trixie ships.
Two separate adaptations are needed on the UNO Q:

1. `PWRecorder` shells out to `pw-record --sample-count=N`. That flag arrives in
   PipeWire 1.5; the UNO Q runs Qualcomm's 1.4.2-1~qcom1 overlay build, where
   pw-record rejects it and every recording fails. Upgrading would displace the
   vendor audio build, so we bound capture with `timeout` instead -- the only
   other way to stop pw-record -- using SIGINT so it finalises the WAV header.
   `timeout` counts wall clock from exec while pw-record needs ~120 ms to start
   streaming, so we over-record by STARTUP_MARGIN_S and trim back to the exact
   requested length. The trim is required, not cosmetic: BaseAudioRecorder
   .check() records 0.1 s and asserts the duration within abs_tol=0.01, and
   BirdNET analyses a fixed 3.0 s window.

2. `PWRecorderConfig.setup()` calls get_input_devices(), whose _parse_pw_info()
   expects the EnumFormat "rate" and "channels" values to be scalars. Multi-rate
   USB mics such as the AudioMoth report them as choice/range objects, so the
   set comprehension raises "unhashable type: 'dict'" and setup stops.
   Enumeration only populates the interactive picker, so we skip it and take the
   node name directly.

Keeping these here rather than editing site-packages means `uv sync` cannot
undo them.
"""

from argparse import ArgumentParser
from pathlib import Path
from subprocess import TimeoutExpired, run

import click
import soundfile as sf

from acoupi.components import PWRecorder, PWRecorderConfig
from acoupi.system.exceptions import (
    DeviceUnavailableError,
    ParameterError,
    RecordingError,
)

# Slack added to the wall-clock limit to cover pw-record's startup latency, so a
# request for N seconds always yields at least N seconds of audio. The surplus is
# trimmed off; this only needs to exceed pw-record's start time. Measured ~120 ms
# on the UNO Q; raise it if recordings ever come back short.
STARTUP_MARGIN_S = 0.4


class PWRecorderCompat(PWRecorder):
    """PWRecorder that stops via `timeout` and trims to an exact duration."""

    def generate_recording(
        self,
        path: Path,
        duration: float | None = None,
    ) -> None:
        duration = duration or self.duration
        wall_clock = duration + STARTUP_MARGIN_S
        cmd = [
            "timeout",
            "--signal=INT",
            f"{wall_clock:.3f}",
            "pw-record",
            f"--rate={self.samplerate}",
            f"--channels={self.audio_channels}",
            f"--target={self.device_name}",
            str(path),
        ]

        path.parent.mkdir(parents=True, exist_ok=True)

        try:
            # `timeout` exits 124 when it fires, which is the normal path here,
            # so the return code is deliberately not checked.
            run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                timeout=wall_clock + 2,
            )
        except FileNotFoundError as error:
            raise DeviceUnavailableError(
                message="The pw-record or timeout command was not found.",
                help="Install PipeWire tools and coreutils; check both are on PATH.",
            ) from error
        except TimeoutExpired as error:
            raise RecordingError(
                message="pw-record did not finish within the expected time.",
                help="Check PipeWire is running and the device is responsive.",
            ) from error

        if not path.exists():
            raise RecordingError(
                message="PipeWire failed to record audio",
                help="Check the microphone supports the requested samplerate.",
            )

        self._trim_to_duration(path, duration)

    def _trim_to_duration(self, path: Path, duration: float) -> None:
        """Cut the over-recorded tail so the file is exactly `duration` long."""
        wanted = int(round(duration * self.samplerate))

        try:
            with sf.SoundFile(path) as recorded:
                frames = len(recorded)
                subtype = recorded.subtype
        except RuntimeError as error:
            raise RecordingError(
                message="pw-record produced an unreadable WAV file.",
                help="Check that pw-record exited cleanly on SIGINT.",
            ) from error

        if frames < wanted:
            raise RecordingError(
                message=(
                    f"Recorded {frames} frames but {wanted} were requested "
                    f"({frames / self.samplerate:.3f}s of {duration:.3f}s)."
                ),
                help=(
                    "pw-record started too slowly; raise STARTUP_MARGIN_S in "
                    "acoupi_yeo_valley.recorder."
                ),
            )

        if frames > wanted:
            data, samplerate = sf.read(path, frames=wanted, dtype="int16")
            sf.write(path, data, samplerate, subtype=subtype)


class PWRecorderCompatConfig(PWRecorderConfig):
    """PWRecorderConfig whose setup() never enumerates PipeWire devices."""

    @classmethod
    def setup(
        cls,
        args: list[str],
        prompt: bool = True,
        prefix: str = "",
    ) -> "PWRecorderCompatConfig":
        parser = ArgumentParser(description="Microphone configuration")
        parser.add_argument(
            f"--{prefix}device-name", dest="device_name", default=None
        )
        parser.add_argument(
            f"--{prefix}samplerate", dest="samplerate", type=int, default=None
        )
        parser.add_argument(
            f"--{prefix}audio-channels",
            dest="audio_channels",
            type=int,
            default=None,
        )
        parsed, _ = parser.parse_known_args(args)

        device_name = parsed.device_name
        if device_name is None:
            if not prompt:
                raise ParameterError(
                    value="device_name",
                    message="No microphone device name provided.",
                    help="Provide --device-name or enable prompting.",
                )
            device_name = click.prompt(
                f"PipeWire node name for {prefix or 'microphone'}", type=str
            )

        samplerate = parsed.samplerate
        if samplerate is None:
            if not prompt:
                raise ParameterError(
                    value="samplerate",
                    message="No samplerate provided.",
                    help="Provide --samplerate or enable prompting.",
                )
            samplerate = click.prompt(
                "Samplerate (Hz)", type=int, default=192_000
            )

        audio_channels = parsed.audio_channels
        if audio_channels is None:
            audio_channels = (
                click.prompt("Audio channels", type=int, default=1)
                if prompt
                else 1
            )

        return cls(
            device_name=device_name,
            samplerate=samplerate,
            audio_channels=audio_channels,
        )
