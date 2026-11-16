import datetime

from acoupi.components import MQTTConfig
from pydantic import BaseModel, Field

from acoupi_yeo_valley.models import BatDetect2Config, BirdNETConfig
from acoupi_yeo_valley.recorder import PWRecorderCompatConfig


class BatRecordingConfig(BaseModel):
    duration: float = 3
    start_recording: datetime.time = datetime.time(hour=0, minute=0, second=0)  # TEST: all-day; revert to 18 for production
    end_recording: datetime.time = datetime.time(hour=23, minute=59, second=59)  # TEST: all-day; revert to 6 for production


class BirdRecordingConfig(BaseModel):
    # 3 s to match the bat recorder: each 30 s slot is then "record 3 s, then
    # ~27 s to detect". BirdNET windows natively at 3 s.
    duration: float = 3


class YeoValleyConfig(BaseModel):
    bird_recorder: PWRecorderCompatConfig
    bat_recorder: PWRecorderCompatConfig
    messages: MQTTConfig
    bats: BatRecordingConfig = Field(default_factory=BatRecordingConfig)
    birds: BirdRecordingConfig = Field(default_factory=BirdRecordingConfig)
    birdnet: BirdNETConfig = Field(default_factory=BirdNETConfig)
    batdetect2: BatDetect2Config = Field(default_factory=BatDetect2Config)
