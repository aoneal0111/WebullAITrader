"""Small, immutable model-card artifacts; datasets are never embedded."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path

from .contracts import MODEL_ARTIFACT_VERSION


@dataclass(frozen=True, slots=True)
class ResearchModelArtifact:
    model_id: str
    generation_id: str
    model_family: str
    feature_version: str
    target_version: str
    training_range: tuple[str, str]
    validation_range: tuple[str, str]
    holdout_range: tuple[str, str]
    hyperparameters: tuple[tuple[str, str], ...]
    training_metrics: tuple[tuple[str, object], ...]
    validation_metrics: tuple[tuple[str, object], ...]
    holdout_metrics: tuple[tuple[str, object], ...]
    feature_names: tuple[str, ...]
    calibration_metadata: tuple[tuple[str, object], ...]
    creation_timestamp: datetime
    artifact_version: str = MODEL_ARTIFACT_VERSION

    def __post_init__(self) -> None:
        if self.creation_timestamp.tzinfo is None or not self.model_id or not self.generation_id:
            raise ValueError("artifact identity and aware timestamp are required")

    @property
    def artifact_hash(self) -> str:
        return sha256(_payload(self).encode()).hexdigest()


def publish_artifact(artifact: ResearchModelArtifact, directory: Path) -> Path:
    """Publish a compact immutable JSON model card using exclusive creation."""

    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{artifact.model_id}.json"
    document = {"artifact": json.loads(_payload(artifact)), "artifact_hash": artifact.artifact_hash}
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(document, stream, sort_keys=True, separators=(",", ":"))
        stream.write("\n")
    return path


def verify_artifact(path: Path) -> bool:
    document = json.loads(path.read_text(encoding="utf-8"))
    payload = json.dumps(document["artifact"], sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode()).hexdigest() == document["artifact_hash"]


def _payload(artifact):
    value = asdict(artifact)
    value["creation_timestamp"] = artifact.creation_timestamp.astimezone(UTC).isoformat()
    return json.dumps(value, sort_keys=True, separators=(",", ":"))
