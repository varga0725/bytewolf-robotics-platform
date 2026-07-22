"""Private ArcFace-compatible embedding representation and 1:1 comparison.

Embeddings are deliberately internal implementation values: the only result
that leaves this module is a scalar cosine similarity with model provenance.
They are intended to be encrypted by ``BiometricTemplateStore`` and must never
be placed in dashboard/API/metadata contracts.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite, sqrt
import struct


_SUPPORTED_DIMENSIONS = frozenset((128, 512))
_SERIALIZATION_VERSION = 1


@dataclass(frozen=True)
class PrivateFaceEmbedding:
    """Internal finite face vector with explicit research-model provenance."""

    model_id: str
    model_version: str
    values: tuple[float, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.model_id, str) or not self.model_id.strip() or not isinstance(self.model_version, str) or not self.model_version.strip():
            raise ValueError("Private face embedding requires model ID and version.")
        if not isinstance(self.values, tuple) or len(self.values) not in _SUPPORTED_DIMENSIONS:
            raise ValueError("Private face embedding dimension is unsupported.")
        if any(not isinstance(value, (int, float)) or isinstance(value, bool) or not isfinite(value) for value in self.values):
            raise ValueError("Private face embedding values must be finite numbers.")
        if not any(float(value) != 0.0 for value in self.values):
            raise ValueError("Private face embedding must be non-zero.")

    def serialize(self) -> bytes:
        """Stable private binary representation for encrypted template storage only."""
        model_id = self.model_id.encode("utf-8")
        model_version = self.model_version.encode("utf-8")
        if len(model_id) > 255 or len(model_version) > 255:
            raise ValueError("Private face embedding model identifiers are too long to serialize.")
        return b"".join((
            struct.pack("!BBBBH", _SERIALIZATION_VERSION, len(model_id), len(model_version), 0, len(self.values)),
            model_id,
            model_version,
            struct.pack(f"!{len(self.values)}f", *self.values),
        ))

    @classmethod
    def deserialize(cls, payload: bytes) -> PrivateFaceEmbedding:
        """Decode an encrypted-store payload; malformed bytes fail closed."""
        if not isinstance(payload, bytes) or len(payload) < 6:
            raise ValueError("Private face embedding payload is malformed.")
        try:
            version, model_id_length, model_version_length, reserved, dimension = struct.unpack("!BBBBH", payload[:6])
            if version != _SERIALIZATION_VERSION or reserved != 0 or dimension not in _SUPPORTED_DIMENSIONS:
                raise ValueError("Private face embedding payload version or dimension is unsupported.")
            header_end = 6 + model_id_length + model_version_length
            expected_size = header_end + dimension * 4
            if len(payload) != expected_size:
                raise ValueError("Private face embedding payload length is invalid.")
            model_id = payload[6:6 + model_id_length].decode("utf-8")
            model_version = payload[6 + model_id_length:header_end].decode("utf-8")
            values = tuple(struct.unpack(f"!{dimension}f", payload[header_end:]))
        except (UnicodeDecodeError, struct.error) as error:
            raise ValueError("Private face embedding payload cannot be decoded.") from error
        return cls(model_id, model_version, values)


@dataclass(frozen=True)
class SimilarityResult:
    """Observation-only scalar similarity; contains no private vector/template."""

    model_id: str
    model_version: str
    similarity: float


class PrivateOneToOneVerifier:
    """Compare same-model private vectors and expose only cosine similarity."""

    def compare(self, probe: PrivateFaceEmbedding, enrolled: PrivateFaceEmbedding) -> SimilarityResult:
        if not isinstance(probe, PrivateFaceEmbedding) or not isinstance(enrolled, PrivateFaceEmbedding):
            raise ValueError("Private 1:1 verification requires private face embeddings.")
        if (probe.model_id, probe.model_version, len(probe.values)) != (enrolled.model_id, enrolled.model_version, len(enrolled.values)):
            raise ValueError("Private 1:1 verification requires matching face embedding models.")
        numerator = sum(float(left) * float(right) for left, right in zip(probe.values, enrolled.values, strict=True))
        probe_norm = sqrt(sum(float(value) ** 2 for value in probe.values))
        enrolled_norm = sqrt(sum(float(value) ** 2 for value in enrolled.values))
        if probe_norm == 0 or enrolled_norm == 0:
            raise ValueError("Private 1:1 verification requires non-zero embeddings.")
        similarity = max(-1.0, min(1.0, numerator / (probe_norm * enrolled_norm)))
        return SimilarityResult(probe.model_id, probe.model_version, similarity)
