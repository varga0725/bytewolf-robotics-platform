"""ByteWolf Plugin SDK.

Versioned plugin contract layer: ``PluginManifest``, ``Capability``,
``ToolPolicy`` and ``PluginHealth`` with a ``register/start/stop/health``
lifecycle (hot-reload included in v0.1), a capability registry, permission/
allowlist handling and dependency/conflict resolution.

Safety boundary: nothing in this package may import or call the MAVSDK adapter,
MAVLink or PX4. Flight-control capabilities are not registrable -- the access
class enum has no actuation value, so such a capability cannot even be
expressed. ``twin.yaml`` and ``brain/safety/gate.py`` remain the single source
of the safety contract.

Deliberately out of scope for v0.1 (deferred to a later contract version):
process isolation / sandboxing, remote/network plugins, and plugin signing /
marketplace provenance. See ``docs/workstreams/plugin-sdk.md``.
"""

from brain.plugin_sdk.contracts import (
    ALLOWED_ACCESS_CLASSES,
    PLUGIN_SDK_CONTRACT_VERSION,
    Capability,
    PluginContractError,
    PluginHealth,
    PluginManifest,
    ToolPolicy,
    load_capability,
    load_plugin_health,
    load_plugin_manifest,
    load_tool_policy,
)
from brain.plugin_sdk.registry import (
    LifecycleState,
    Plugin,
    PluginRegistry,
    PluginRegistryError,
    version_satisfies,
)

CONTRACT_VERSION = PLUGIN_SDK_CONTRACT_VERSION

__all__ = [
    "ALLOWED_ACCESS_CLASSES",
    "CONTRACT_VERSION",
    "PLUGIN_SDK_CONTRACT_VERSION",
    "Capability",
    "LifecycleState",
    "Plugin",
    "PluginContractError",
    "PluginHealth",
    "PluginManifest",
    "PluginRegistry",
    "PluginRegistryError",
    "ToolPolicy",
    "load_capability",
    "load_plugin_health",
    "load_plugin_manifest",
    "load_tool_policy",
    "version_satisfies",
]
