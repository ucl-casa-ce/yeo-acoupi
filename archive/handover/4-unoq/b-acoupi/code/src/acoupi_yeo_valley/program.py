import datetime
from functools import partial
from pathlib import Path
from typing import Sequence

from acoupi.components import (
    DetectionThresholdMessageBuilder,
    IsInInterval,
    MQTTMessenger,
    PWRecorder,  # noqa: F401  (base class for PWRecorderCompat)
    SqliteMessageStore,
    SqliteStore,
)
from acoupi.components.recording_conditions import HasSufficientSpace
from acoupi.devices.metrics import (
    consumed_memory,
    get_cpu_usage,
    get_free_memory,
    get_remaining_storage,
)
from acoupi.programs import AcoupiProgram
from acoupi.programs.core import AcoupiWorker, WorkerConfig
from acoupi.system.files import get_temp_dir
from celery.schedules import crontab

from acoupi import data, tasks
from acoupi_yeo_valley.components import DetectionCountByTagSummary
from acoupi_yeo_valley.config import YeoValleyConfig
from acoupi_yeo_valley.recorder import PWRecorderCompat
from acoupi_yeo_valley.models import BatDetect2Model, BirdNETModel


class YeoValleyProgram(AcoupiProgram):
    config_schema = YeoValleyConfig

    worker_config = WorkerConfig(
        workers=[
            AcoupiWorker(
                name="recording",
                queues=["recording"],
                concurrency=1,
            ),
            AcoupiWorker(
                name="default",
                queues=["celery"],
            ),
        ],
    )

    def setup(self, config: YeoValleyConfig):
        self.logger.info("Setting up program")

        super().setup(config)

        self.audio_dir = get_temp_dir()

        self.store = SqliteStore(Path.home() / "acoupi.db")
        self.message_store = SqliteMessageStore(Path.home() / "messages.db")

        # --- DETECTION ---
        # - BIRDS -
        self.bird_model = BirdNETModel.from_config(config.birdnet)
        detect_birds = tasks.generate_detection_task(
            store=self.store,
            model=self.bird_model,
            # TESTING: without a message factory no detection messages are ever
            # created (only the hourly summaries). This emits one message per
            # recording, holding every detection above the model threshold.
            # For production see docs/ACOUPI_CELLULAR_PLAN.md - birds should be
            # enumerated into one batched message, not sent per recording.
            message_factories=[
                DetectionThresholdMessageBuilder(
                    detection_threshold=config.birdnet.detection_threshold
                )
            ],
            message_store=self.message_store,
        )
        detect_birds.__name__ = "detect_birds"  # type: ignore

        # - BATS -
        self.bat_model = BatDetect2Model.from_config(config.batdetect2)
        detect_bats = tasks.generate_detection_task(
            store=self.store,
            model=self.bat_model,
            # TESTING: see note on detect_birds. Bats must be summarised for
            # production - a dense night is ~3,600 detections/hour.
            message_factories=[
                DetectionThresholdMessageBuilder(
                    detection_threshold=config.batdetect2.detection_threshold
                )
            ],
            message_store=self.message_store,
        )
        detect_bats.__name__ = "detect_bats"  # type: ignore

        # --- RECORDING ---

        # Condition to check if there are at least 2 MB of space available
        has_space = HasSufficientSpace(
            path=self.audio_dir, min_space=2, unit="MB"
        )

        # - BATS -
        self.bat_recorder = PWRecorderCompat(
            duration=config.bats.duration,
            audio_channels=1,
            samplerate=config.bat_recorder.samplerate,
            device_name=config.bat_recorder.device_name,
            audio_dir=Path(self.audio_dir.name),
        )

        self.add_task(
            tasks.generate_recording_task(
                recorder=self.bat_recorder,
                store=self.store,
                recording_conditions=[
                    has_space,
                    IsInInterval(
                        interval=data.TimeInterval(
                            start=config.bats.start_recording,
                            end=config.bats.end_recording,
                        ),
                        timezone=datetime.timezone.utc,
                    ),
                ],
            ),
            schedule=tasks.aligned_schedule(
                run_every=datetime.timedelta(seconds=60),
                offset_seconds=0,
            ),
            callbacks=[detect_bats],
            name="bat_recording",
            queue="recording",
        )

        # - BIRDS -
        self.bird_recorder = PWRecorderCompat(
            duration=config.birds.duration,
            audio_channels=1,
            samplerate=config.bird_recorder.samplerate,
            device_name=config.bird_recorder.device_name,
            audio_dir=Path(self.audio_dir.name),
        )

        self.add_task(
            tasks.generate_recording_task(
                recorder=self.bird_recorder,
                store=self.store,
                recording_conditions=[has_space],
            ),
            schedule=tasks.aligned_schedule(
                run_every=datetime.timedelta(seconds=60),
                offset_seconds=30,
            ),
            callbacks=[detect_birds],
            name="bird_recording",
            queue="recording",
        )

        # --- FILE MANAGEMENT ---
        # Check that the recording has been processed
        def has_been_processed(
            recording: data.Recording,
            outputs: Sequence[data.ModelOutputInfo],
        ) -> bool:
            # This guard decides whether a recording may be deleted.
            #
            # It previously keyed off samplerate: >90 kHz required the bat model
            # to have run, <90 kHz required the bird model. That assumed the two
            # recorders used different samplerates. With BOTH recorders at
            # 192 kHz, a bird recording is >90 kHz but only ever sees the bird
            # model, so neither branch matched, it was never marked processed,
            # and audio accumulated in the shm audio dir until the device died.
            #
            # Models are chosen by the recording task's callback, not by
            # samplerate, so the correct test is simply: did a model run?
            model_names = {output.name_model for output in outputs}
            return bool(
                model_names & {self.bat_model.name, self.bird_model.name}
            )

        self.add_task(
            tasks.generate_file_management_task(
                store=self.store,
                file_managers=[],  # No file managers means delete everything
                management_conditions=[has_been_processed],
            ),
            schedule=datetime.timedelta(seconds=60 * 5),
            name="file_management",
        )

        # --- MESSAGING ---
        self.messenger = MQTTMessenger.from_config(config.messages)

        # - SEND MESSAGES -
        self.add_task(
            tasks.generate_send_messages_task(
                message_store=self.message_store,
                messengers=[self.messenger],
            ),
            # TESTING: drain the message store almost immediately so each
            # detection reaches the broker within ~15 s of the recording.
            # Production wants hourly (or once/twice daily) batches - see
            # docs/CELLULAR_DATA_BUDGET.md.
            schedule=datetime.timedelta(seconds=15),
            name="send_messages",
        )

        # - SEND HEARTBEATS -
        self.add_task(
            tasks.generate_heartbeat_task(
                messengers=[self.messenger],
                metrics=[
                    partial(get_remaining_storage, self.audio_dir, name="shm"),
                    get_free_memory,
                    get_cpu_usage,
                    consumed_memory,
                ],
            ),
            schedule=datetime.timedelta(seconds=30),
            # schedule=datetime.timedelta(seconds=60 * 60),  # Every hour
            name="send_heartbeats",
        )

        # - GENERATE SUMMARIES
        self.add_task(
            tasks.generate_summariser_task(
                message_store=self.message_store,
                summarisers=[
                    # TODO: add summarisers for all interesting species
                    DetectionCountByTagSummary(
                        store=self.store,
                        key="species",
                        value="Pipistrellus pipistrellus",
                        min_tag_score=0.5,
                    ),
                    DetectionCountByTagSummary(
                        store=self.store,
                        key="species",
                        value="Troglodytes troglodytes",
                        min_tag_score=0.5,
                    ),
                ],
            ),
            schedule=crontab(hour="*", minute=0),
            name="generate_summaries",
        )

    def check(self, config: YeoValleyConfig) -> None:
        self.logger.info("Checking program")

        self.bird_recorder.check()
        self.bat_recorder.check()
        self.messenger.check()

        self.logger.info("Program check complete")
