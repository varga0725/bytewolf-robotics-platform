"""Turn a plugin's capability requests into a fail-closed ToolPolicy.

The manifest states what a plugin *wants* to consume (``requests``); this engine
decides what it may *actually* consume and records the decision as a
``ToolPolicy``. Nothing is granted unless every check passes, and every refusal
carries a reason, so a denial is auditable rather than silent.

A request is granted only when all of these hold:

* its capability-id namespace is not on the forbidden (flight-control) denylist;
* the capability-id is on the caller-supplied allowlist;
* a registered plugin provides that capability;
* the provider offers the exact requested capability version.

Otherwise it is denied with the first failing reason. The forbidden-namespace
check is the same one the registry applies to ``provides``, so a
flight-control-shaped capability can neither be offered nor consumed.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from brain.plugin_sdk.contracts import (
    PluginManifest,
    ToolPolicy,
    is_forbidden_capability,
)
from brain.plugin_sdk.registry import PluginRegistry


def build_tool_policy(
    manifest: PluginManifest,
    registry: PluginRegistry,
    allowlist: Iterable[str],
    limits: dict[str, Any] | None = None,
) -> ToolPolicy:
    """Derive the fail-closed ToolPolicy for a plugin's requests."""
    allowed = set(allowlist)
    provider_index = _provider_index(registry)

    granted: list[dict[str, str]] = []
    denied: list[dict[str, str]] = []

    for request in manifest.requests:
        capability_id = request["capability_id"]
        version = request["version"]
        reason = _refusal_reason(capability_id, version, allowed, provider_index)
        if reason is None:
            granted.append({"capability_id": capability_id, "version": version})
        else:
            denied.append({"capability_id": capability_id, "reason": reason})

    return ToolPolicy(
        plugin_id=manifest.plugin_id,
        granted=tuple(granted),
        denied=tuple(denied),
        limits=limits,
    )


def _refusal_reason(
    capability_id: str,
    version: str,
    allowed: set[str],
    provider_index: dict[str, set[str]],
) -> str | None:
    """Why this request must be refused, or None if it may be granted."""
    if is_forbidden_capability(capability_id):
        return "forbidden namespace"
    if capability_id not in allowed:
        return "not on allowlist"
    versions = provider_index.get(capability_id)
    if not versions:
        return "no registered provider"
    if version not in versions:
        return f"version {version} unavailable"
    return None


def _provider_index(registry: PluginRegistry) -> dict[str, set[str]]:
    """Map each provided capability id to the versions registered providers offer."""
    index: dict[str, set[str]] = {}
    for plugin_id in registry.plugin_ids():
        manifest = registry.manifest(plugin_id)
        for capability in manifest.provides:
            index.setdefault(capability.capability_id, set()).add(capability.version)
    return index
