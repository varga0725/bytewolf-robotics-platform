"""PX4 / MAVLink parameter value decoding (PARAM_VALUE wire format)."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Mapping

# MAVLink parameter types (common dialect).
MAV_PARAM_TYPE_UINT8 = 1
MAV_PARAM_TYPE_INT8 = 2
MAV_PARAM_TYPE_UINT16 = 3
MAV_PARAM_TYPE_INT16 = 4
MAV_PARAM_TYPE_UINT32 = 5
MAV_PARAM_TYPE_INT32 = 6
MAV_PARAM_TYPE_UINT64 = 7
MAV_PARAM_TYPE_INT64 = 8
MAV_PARAM_TYPE_REAL32 = 9
MAV_PARAM_TYPE_REAL64 = 10

_INTEGER_TYPES = frozenset(
    {
        MAV_PARAM_TYPE_UINT8,
        MAV_PARAM_TYPE_INT8,
        MAV_PARAM_TYPE_UINT16,
        MAV_PARAM_TYPE_INT16,
        MAV_PARAM_TYPE_UINT32,
        MAV_PARAM_TYPE_INT32,
        MAV_PARAM_TYPE_UINT64,
        MAV_PARAM_TYPE_INT64,
    }
)


@dataclass(frozen=True)
class ParamValue:
    """Decoded parameter plus the MAVLink type PX4 declared."""

    value: int | float
    param_type: int

    @property
    def is_integer(self) -> bool:
        return self.param_type in _INTEGER_TYPES

    @property
    def display_value(self) -> int | float:
        if self.is_integer:
            return int(self.value)
        return float(self.value)


def decode_mavlink_param_value(raw: float, param_type: int) -> ParamValue:
    """Decode ``PARAM_VALUE.param_value`` using ``PARAM_VALUE.param_type``.

    PX4 sends integer parameters by bit-casting into the float32 field.
    """

    bits = struct.pack("<f", float(raw))
    if param_type == MAV_PARAM_TYPE_UINT8:
        value: int | float = struct.unpack("<B", bits[:1])[0]
    elif param_type == MAV_PARAM_TYPE_INT8:
        value = struct.unpack("<b", bits[:1])[0]
    elif param_type == MAV_PARAM_TYPE_UINT16:
        value = struct.unpack("<H", bits[:2])[0]
    elif param_type == MAV_PARAM_TYPE_INT16:
        value = struct.unpack("<h", bits[:2])[0]
    elif param_type == MAV_PARAM_TYPE_UINT32:
        value = struct.unpack("<I", bits)[0]
    elif param_type == MAV_PARAM_TYPE_INT32:
        value = struct.unpack("<i", bits)[0]
    elif param_type in {MAV_PARAM_TYPE_UINT64, MAV_PARAM_TYPE_INT64}:
        # PARAM_VALUE only carries four bytes; PX4 rarely exposes wider types here.
        value = struct.unpack("<i", bits)[0]
    elif param_type in {MAV_PARAM_TYPE_REAL32, MAV_PARAM_TYPE_REAL64}:
        value = struct.unpack("<f", bits)[0]
    else:
        value = float(raw)
    return ParamValue(value=value, param_type=param_type)


def encode_px4_int32_wire_value(value: int) -> float:
    """Encode an INT32 parameter the way PX4 places it in ``PARAM_VALUE.param_value``."""

    return struct.unpack("<f", struct.pack("<i", int(value)))[0]


def encode_px4_real32_wire_value(value: float) -> float:
    """Encode a REAL32 parameter in the ``PARAM_VALUE`` float field."""

    return struct.unpack("<f", struct.pack("<f", float(value)))[0]


def snapshot_numeric_values(params: Mapping[str, ParamValue]) -> dict[str, int | float]:
    """Rule evaluation uses scalar values keyed by parameter name."""

    return {name: entry.display_value for name, entry in params.items()}


def param_value_from_qgc_export(raw: float) -> ParamValue:
    """Offline QGC exports carry no MAVLink type; treat whole numbers as integer-like."""

    if float(raw).is_integer() and abs(raw) < 1 << 31:
        return ParamValue(value=int(raw), param_type=MAV_PARAM_TYPE_INT32)
    return ParamValue(value=float(raw), param_type=MAV_PARAM_TYPE_REAL32)