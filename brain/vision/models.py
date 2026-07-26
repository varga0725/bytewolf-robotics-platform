"""Explicit registry separating research models from release-approved models."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ModelStage(str, Enum):
    RESEARCH = "research"
    PRODUCTION = "production"


@dataclass(frozen=True)
class ModelRecord:
    model_id: str
    version: str
    stage: ModelStage
    weights_path: str
    license_reference: str | None = None

    def __post_init__(self) -> None:
        if not self.model_id or not self.version or not self.weights_path:
            raise ValueError("Model records require ID, version, and local weights path.")
        if self.stage is ModelStage.PRODUCTION and not self.license_reference:
            raise ValueError("Production model records require a license reference.")


class ModelRegistry:
    def __init__(self, records: tuple[ModelRecord, ...]) -> None:
        self._records = {record.model_id: record for record in records}
        if len(self._records) != len(records):
            raise ValueError("Model IDs must be unique.")

    def resolve(self, model_id: str, *, public_release: bool) -> ModelRecord:
        try:
            record = self._records[model_id]
        except KeyError as error:
            raise ValueError(f"Unknown Vision model: {model_id}") from error
        if public_release and record.stage is not ModelStage.PRODUCTION:
            raise ValueError("A research model cannot be selected for a public release.")
        return record
