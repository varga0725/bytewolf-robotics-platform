"""Load and validate the Plugin SDK v0.1 contracts, failing closed.

Four versioned documents make up the contract layer:

* ``PluginManifest`` - what a plugin claims about itself: identity, release
  version, the capabilities it provides, its dependencies and conflicts, and the
  capabilities it requests to consume.
* ``Capability``     - one named, versioned unit of function. Its ``access`` class
  is a fail-closed positive enum with no actuation value, so a flight-control
  capability cannot even be expressed.
* ``ToolPolicy``     - the registry's fail-closed decision about what a plugin may
  actually consume. The plugin proposes (manifest ``requests``); the registry
  disposes (``granted`` / ``denied``).
* ``PluginHealth``   - a runtime snapshot that, like an observation, carries its
  own freshness so a long-stopped reading is not read as current.

Each loader validates against the frozen JSON Schema and refuses anything it
cannot fully trust, rather than returning a best-effort value. Implementation of
the registry, lifecycle and hot-reload builds on these types; see
``docs/workstreams/plugin-sdk.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
import json
from pathlib import Path
from typing import Any

import jsonschema


PLUGIN_SDK_CONTRACT_VERSION = "v0.1"

_SCHEMA_DIR = Path(__file__).resolve().parents[2] / "shared/schemas/plugin_sdk"
CAPABILITY_SCHEMA_PATH = _SCHEMA_DIR / "capability_v0_1.schema.json"
PLUGIN_MANIFEST_SCHEMA_PATH = _SCHEMA_DIR / "plugin_manifest_v0_1.schema.json"
TOOL_POLICY_SCHEMA_PATH = _SCHEMA_DIR / "tool_policy_v0_1.schema.json"
PLUGIN_HEALTH_SCHEMA_PATH = _SCHEMA_DIR / "plugin_health_v0_1.schema.json"

#: Access classes a capability may declare. Deliberately positive and closed:
#: there is no actuation class, so a flight-control capability cannot be named.
ALLOWED_ACCESS_CLASSES = frozenset({"read", "query", "propose", "communicate"})

#: Capability-id namespaces that may never be provided or granted, whatever
#: access class they claim. The access enum already forbids an actuation class,
#: but a capability_id is free text, so a plugin could name a 'read' capability
#: 'flight.arm'. This code-layer denylist closes that gap: the namespace (the
#: segment before the first separator) is checked at registration and again when
#: a ToolPolicy is built, so a flight-control-shaped id cannot slip through.
FORBIDDEN_CAPABILITY_NAMESPACES = frozenset(
    {
        "flight",
        "mavsdk",
        "mavlink",
        "px4",
        "actuate",
        "control",
        "arm",
        "motor",
        "actuator",
        "setpoint",
        "offboard",
    }
)


def capability_namespace(capability_id: str) -> str:
    """The leading segment of a capability id, e.g. 'flight' in 'flight.arm'."""
    for separator in (".", "_", "-"):
        capability_id = capability_id.replace(separator, ".")
    return capability_id.split(".", 1)[0]


def is_forbidden_capability(capability_id: str) -> bool:
    """Whether a capability id sits in a namespace that may never be granted."""
    return capability_namespace(capability_id) in FORBIDDEN_CAPABILITY_NAMESPACES


class PluginContractError(ValueError):
    """Raised when a document cannot be read as a Plugin SDK contract."""


@dataclass(frozen=True)
class Capability:
    """One named, versioned unit of function a plugin provides or requests."""

    capability_id: str
    version: str
    access: str
    data_contract: dict[str, Any] | None
    description: str | None


@dataclass(frozen=True)
class PluginManifest:
    """A plugin's own claim about what it is, provides, requires and requests."""

    plugin_id: str
    version: str
    name: str
    description: str | None
    provides: tuple[Capability, ...]
    requires: tuple[dict[str, Any], ...]
    conflicts: tuple[str, ...]
    requests: tuple[dict[str, Any], ...]
    reloadable: bool
    state_migration: str | None


@dataclass(frozen=True)
class ToolPolicy:
    """The registry's fail-closed grant/deny decision for one plugin."""

    plugin_id: str
    granted: tuple[dict[str, Any], ...]
    denied: tuple[dict[str, Any], ...]
    limits: dict[str, Any] | None


@dataclass(frozen=True)
class PluginHealth:
    """A runtime health snapshot with its own freshness window."""

    plugin_id: str
    lifecycle_state: str
    health: str
    checked_at: datetime
    max_age_s: float
    detail: str | None

    def age_s(self, now: datetime) -> float:
        """Seconds since the snapshot was taken, never negative."""
        return max(0.0, (_utc(now) - self.checked_at).total_seconds())

    def is_fresh(self, now: datetime) -> bool:
        """A snapshot older than its own max_age_s must not be treated as current."""
        return self.age_s(now) <= self.max_age_s


@lru_cache(maxsize=None)
def _validator(schema_path: Path) -> Any:
    """Compile a contract once rather than once per document."""
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except OSError as error:
        raise PluginContractError(
            f"Cannot read the plugin-sdk schema '{schema_path}': {error.strerror}."
        ) from error
    validator_class = jsonschema.validators.validator_for(schema)
    validator_class.check_schema(schema)
    return validator_class(schema)


def _validate(document: object, schema_path: Path, label: str) -> dict[str, Any]:
    try:
        _validator(schema_path).validate(document)
    except jsonschema.ValidationError as error:
        location = "/".join(str(part) for part in error.absolute_path) or "<root>"
        raise PluginContractError(f"{label} rejected at '{location}': {error.message}") from error
    assert isinstance(document, dict), "The schema requires an object at the root."
    return document


def _capability(document: dict[str, Any]) -> Capability:
    return Capability(
        capability_id=document["capability_id"],
        version=document["version"],
        access=document["access"],
        data_contract=document.get("data_contract"),
        description=document.get("description"),
    )


def load_capability(document: object) -> Capability:
    """Read a document as a bare Capability, or refuse it."""
    validated = _validate(document, CAPABILITY_SCHEMA_PATH, "Capability")
    return _capability(validated)


def load_plugin_manifest(document: object) -> PluginManifest:
    """Read a document as a PluginManifest, or refuse it."""
    validated = _validate(document, PLUGIN_MANIFEST_SCHEMA_PATH, "PluginManifest")
    return PluginManifest(
        plugin_id=validated["plugin_id"],
        version=validated["version"],
        name=validated["name"],
        description=validated.get("description"),
        provides=tuple(_capability(item) for item in validated["provides"]),
        requires=tuple(validated.get("requires", [])),
        conflicts=tuple(validated.get("conflicts", [])),
        requests=tuple(validated.get("requests", [])),
        reloadable=bool(validated.get("reloadable", False)),
        state_migration=validated.get("state_migration"),
    )


def load_tool_policy(document: object) -> ToolPolicy:
    """Read a document as a ToolPolicy, or refuse it."""
    validated = _validate(document, TOOL_POLICY_SCHEMA_PATH, "ToolPolicy")
    return ToolPolicy(
        plugin_id=validated["plugin_id"],
        granted=tuple(validated["granted"]),
        denied=tuple(validated.get("denied", [])),
        limits=validated.get("limits"),
    )


def load_plugin_health(document: object) -> PluginHealth:
    """Read a document as a PluginHealth snapshot, or refuse it."""
    validated = _validate(document, PLUGIN_HEALTH_SCHEMA_PATH, "PluginHealth")
    return PluginHealth(
        plugin_id=validated["plugin_id"],
        lifecycle_state=validated["lifecycle_state"],
        health=validated["health"],
        checked_at=_parse_timestamp(validated["checked_at"]),
        max_age_s=float(validated["max_age_s"]),
        detail=validated.get("detail"),
    )


def _parse_timestamp(value: str) -> datetime:
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise PluginContractError(f"Health timestamp '{value}' is not RFC 3339.") from error
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise PluginContractError(
            f"Health timestamp '{value}' has no offset; an age cannot be measured from it."
        )
    return timestamp.astimezone(UTC)


def _utc(moment: datetime) -> datetime:
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise PluginContractError("The current time must be timezone-aware to measure an age.")
    return moment.astimezone(UTC)
