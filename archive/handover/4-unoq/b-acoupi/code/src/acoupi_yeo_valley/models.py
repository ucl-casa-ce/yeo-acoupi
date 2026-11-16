import os
from pathlib import Path
from typing import TYPE_CHECKING

from acoupi.components.types import Model
from acoupi.data import (
    BoundingBox,
    ModelOutput,
    PredictedTag,
    PresenceDetection,
    Recording,
    Tag,
)
from acoupi.system.exceptions import ParameterError
from pydantic import BaseModel

if TYPE_CHECKING:
    import birdnet
    from batdetect2 import BatDetect2API


class BatDetect2Config(BaseModel):
    detection_threshold: float = 0.3


class BatDetect2Model(Model):
    name = "batdetect2"

    def __init__(self, detection_threshold: float = 0.3):
        self.detection_threshold = detection_threshold
        self._api = None

    @classmethod
    def from_config(cls, config: BatDetect2Config) -> "BatDetect2Model":
        return cls(detection_threshold=config.detection_threshold)

    @property
    def api(self) -> "BatDetect2API":
        # acoupi targets CPU-only edge devices (e.g. Raspberry Pi), and
        # BatDetect2 runs inference through a PyTorch Lightning ``Trainer``
        # whose default ``accelerator="auto"`` grabs any CUDA device it can
        # see. On a host with a GPU whose driver is older than the bundled
        # torch build, that auto-selection raises at CUDA init. Hide CUDA
        # before torch is first imported so inference runs on CPU; an
        # operator who wants a GPU can still export CUDA_VISIBLE_DEVICES.
        os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")

        from batdetect2 import BatDetect2API

        if self._api is not None:
            return self._api

        self._api = BatDetect2API.from_checkpoint()
        return self._api

    def run(self, recording: Recording) -> ModelOutput:
        import numpy as np

        if not recording.path or not recording.path.exists():
            raise ValueError(f"Recording {recording} was not found")

        clip_detections = self.api.process_file(
            recording.path,
            detection_threshold=self.detection_threshold,
        )

        class_names = self.api.targets.class_names

        detections = []

        for det in clip_detections.detections:
            top_index = int(np.argmax(det.class_scores))
            score = det.class_scores[top_index]
            detections.append(
                PresenceDetection(
                    location=BoundingBox(coordinates=det.geometry.coordinates),  # type: ignore
                    detection_score=score,
                    tags=[
                        PredictedTag(
                            tag=Tag(
                                key="species", value=class_names[top_index]
                            ),
                            confidence_score=score,
                        )
                    ],
                )
            )

        return ModelOutput(
            name_model=self.name,
            recording=recording,
            detections=detections,
        )


class BirdNETConfig(BaseModel):
    detection_threshold: float = 0.3
    location_score_threshold: float = 0.3
    use_location: bool = False
    use_week: bool = False
    use_common_name: bool = False
    # acoupi's config parser handles str/int/float/bool; Path|None has no
    # handler, so `acoupi setup` aborts when it reaches this field. Empty = unset.
    species_list_file: str = ""


class BirdNETModel(Model):
    name = "birdnet_2.4"

    def __init__(
        self,
        detection_threshold: float = 0.3,
        with_geo: bool = False,
        geo_score_threshold: float = 0.3,
        species_list: list[str] | None = None,
        use_common_name: bool = False,
        use_week: bool = False,
    ):
        self.detection_threshold = detection_threshold
        self.with_geo = with_geo
        self.species_list = species_list
        self.geo_score_threshold = geo_score_threshold
        self.use_week = use_week
        self.use_common_name = use_common_name
        self._model = None
        self._geo_model = None
        self._list_cache = {"key": None, "value": None}

    @property
    def model(self) -> "birdnet.AcousticModelV2_4":
        import birdnet

        if self._model is not None:
            return self._model

        self._model = birdnet.load("acoustic", "2.4", "tf")
        return self._model

    @property
    def geo_model(self) -> "birdnet.GeoModelV2_4":
        import birdnet

        if self._geo_model is not None:
            return self._geo_model

        self._geo_model = birdnet.load("geo", "2.4", "tf")
        return self._geo_model

    def get_species_list(self, recording: Recording) -> list[str] | None:
        if self.species_list:
            return self.species_list

        if not self.geo_model:
            return None

        deployment = recording.deployment

        week = None
        if self.use_week:
            week = recording.created_on.isocalendar()[1]

        if deployment.latitude is None or deployment.longitude is None:
            return None

        key = (deployment.latitude, deployment.longitude, week)

        if key == self._list_cache["key"]:
            return self._list_cache["value"]

        results = self.geo_model.predict(
            deployment.latitude,
            deployment.longitude,
            week=week,
        )

        species_list = [
            str(species)
            for species, score in zip(
                results.species_list, results.species_probs
            )
            if score > self.geo_score_threshold
        ]

        self._list_cache["key"] = key
        self._list_cache["value"] = species_list
        return species_list

    @classmethod
    def from_config(cls, config: BirdNETConfig) -> "BirdNETModel":
        species_list = None

        if config.species_list_file:
            if not Path(config.species_list_file).exists():
                raise ParameterError(
                    value=str(config.species_list_file),
                    message=f"Species list file {config.species_list_file} does not exist",
                    help="Check the path to the species list file",
                )

            species_list = Path(config.species_list_file).read_text().splitlines()

        return cls(
            detection_threshold=config.detection_threshold,
            with_geo=config.use_location,
            geo_score_threshold=config.location_score_threshold,
            species_list=species_list,
            use_common_name=config.use_common_name,
            use_week=config.use_week,
        )

    def run(self, recording: Recording) -> ModelOutput:
        if not recording.path or not recording.path.exists():
            raise ValueError(f"Recording {recording} was not found")

        predictions = self.model.predict(
            recording.path,
            default_confidence_threshold=self.detection_threshold,
            half_precision=True,
            custom_species_list=self.species_list,
        )

        array = predictions.to_structured_array()
        return ModelOutput(
            name_model=self.name,
            recording=recording,
            detections=[
                PresenceDetection(
                    location=BoundingBox(
                        coordinates=(
                            float(start_time),
                            0,
                            float(end_time),
                            min(recording.samplerate / 2, 15000),
                        )
                    ),
                    detection_score=score,
                    tags=[self.get_detection_tag(class_name, score)],
                )
                for (_, start_time, end_time, class_name, score) in array
            ],
        )

    def get_detection_tag(self, class_name: str, score: float) -> PredictedTag:
        species_name, common_name = class_name.split("_")

        if self.use_common_name:
            return PredictedTag(
                tag=Tag(key="common_name", value=common_name),
                confidence_score=score,
            )

        return PredictedTag(
            tag=Tag(key="species", value=species_name),
            confidence_score=score,
        )

    def check(self) -> bool:
        try:
            self.model
            return True
        except Exception:
            return False
