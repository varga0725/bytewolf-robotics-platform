"""ByteWolf Plugin SDK.

Versioned plugin contract layer: ``PluginManifest``, ``Capability``,
``ToolPolicy`` and ``PluginHealth`` with a ``register/start/stop/health``
lifecycle, a capability registry, permission/allowlist handling and
dependency/conflict resolution.

Safety boundary: nothing in this package may import or call the MAVSDK adapter,
MAVLink or PX4. Flight-control capabilities are not registrable. ``twin.yaml``
and ``brain/safety/gate.py`` remain the single source of the safety contract.

See ``docs/workstreams/plugin-sdk.md`` for scope, the versioned-contract plan,
the Definition of Done and the acceptance criteria.
"""

CONTRACT_VERSION = "v0.1"

__all__ = ["CONTRACT_VERSION"]
