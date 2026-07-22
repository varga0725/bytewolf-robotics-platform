"""In-process plugin registry and lifecycle for the Plugin SDK v0.1.

The registry owns a plugin from ``register`` through ``start`` /
``reload`` / ``stop``, and refuses anything that would leave the set in an
inconsistent state:

* a duplicate ``plugin_id`` or a second provider of the same capability;
* a declared conflict against something already registered (checked both ways);
* a missing or version-incompatible dependency at start time;
* a circular dependency;
* stopping a plugin another *started* plugin still depends on;
* reloading a plugin whose manifest is not ``reloadable``.

It never partially starts: a plugin that fails a precondition raises before its
``start`` hook runs. v0.1 is in-process and trust-bounded -- a plugin is a plain
Python object with optional ``start`` / ``stop`` / ``reload`` / ``health`` hooks.
Isolation, remote transport and signing are deferred (see the package docstring).

The registry grants no capability: an ``access`` class cannot name actuation, and
nothing here imports MAVSDK or PX4. Turning a manifest's requests into an
enforced grant is the ToolPolicy engine's job, built on top of this.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from brain.plugin_sdk.contracts import PluginHealth, PluginManifest


class PluginRegistryError(ValueError):
    """Raised when a lifecycle operation would break registry consistency."""


class LifecycleState(str, Enum):
    """Where a registered plugin sits in register -> start -> (reload) -> stop."""

    REGISTERED = "registered"
    STARTED = "started"
    RELOADING = "reloading"
    STOPPED = "stopped"
    FAILED = "failed"


@runtime_checkable
class Plugin(Protocol):
    """Optional in-process lifecycle hooks a plugin object may implement.

    Every hook is optional: a manifest-only plugin (no behaviour, useful for
    dependency and contract tests) is registrable and startable. Missing hooks
    are simply not called.
    """

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def reload(self) -> None: ...
    def health(self) -> str: ...


@dataclass
class _Registered:
    manifest: PluginManifest
    instance: Any | None
    state: LifecycleState = LifecycleState.REGISTERED
    health: str = "unknown"
    detail: str | None = None


class PluginRegistry:
    """Holds registered plugins and drives their lifecycle, failing closed."""

    def __init__(self) -> None:
        self._plugins: dict[str, _Registered] = {}

    # -- registration -----------------------------------------------------

    def register(self, manifest: PluginManifest, instance: Any | None = None) -> None:
        """Admit a plugin, rejecting duplicates, conflicts and cycles."""
        if manifest.plugin_id in self._plugins:
            raise PluginRegistryError(f"Plugin '{manifest.plugin_id}' is already registered.")

        self._reject_capability_clashes(manifest)
        self._reject_conflicts(manifest)

        self._plugins[manifest.plugin_id] = _Registered(manifest=manifest, instance=instance)

        # A cycle can only exist once every plugin on it is present, so it is
        # cheapest and clearest to check the whole graph here. If this manifest
        # closes a loop, unregister it and refuse rather than leave it in.
        cycle = self._find_cycle()
        if cycle is not None:
            del self._plugins[manifest.plugin_id]
            raise PluginRegistryError(
                "Circular dependency: " + " -> ".join(cycle) + "."
            )

    def _reject_capability_clashes(self, manifest: PluginManifest) -> None:
        provided = {cap.capability_id for cap in manifest.provides}
        for other in self._plugins.values():
            clash = provided & {cap.capability_id for cap in other.manifest.provides}
            if clash:
                raise PluginRegistryError(
                    f"Capability {sorted(clash)} is already provided by "
                    f"'{other.manifest.plugin_id}'."
                )

    def _reject_conflicts(self, manifest: PluginManifest) -> None:
        mine = {manifest.plugin_id} | {cap.capability_id for cap in manifest.provides}
        my_conflicts = set(manifest.conflicts)
        for other in self._plugins.values():
            theirs = {other.manifest.plugin_id} | {
                cap.capability_id for cap in other.manifest.provides
            }
            if my_conflicts & theirs:
                raise PluginRegistryError(
                    f"'{manifest.plugin_id}' declares a conflict with "
                    f"'{other.manifest.plugin_id}'."
                )
            if set(other.manifest.conflicts) & mine:
                raise PluginRegistryError(
                    f"'{other.manifest.plugin_id}' declares a conflict with "
                    f"'{manifest.plugin_id}'."
                )

    # -- lifecycle --------------------------------------------------------

    def start(self, plugin_id: str) -> None:
        """Start a plugin after its dependencies, or refuse and stay put."""
        self._start(plugin_id, starting=set())

    def _start(self, plugin_id: str, starting: set[str]) -> None:
        record = self._require(plugin_id)
        if record.state is LifecycleState.STARTED:
            return
        if plugin_id in starting:
            raise PluginRegistryError(
                "Circular dependency while starting "
                + " -> ".join([*starting, plugin_id])
                + "."
            )
        starting.add(plugin_id)

        for requirement in record.manifest.requires:
            self._resolve_requirement(plugin_id, requirement)
            self._start(requirement["plugin_id"], starting)

        starting.discard(plugin_id)
        self._call_hook(record, "start")
        record.state = LifecycleState.STARTED

    def _resolve_requirement(self, plugin_id: str, requirement: dict[str, Any]) -> None:
        dep_id = requirement["plugin_id"]
        dep = self._plugins.get(dep_id)
        if dep is None:
            raise PluginRegistryError(
                f"'{plugin_id}' requires '{dep_id}', which is not registered."
            )
        version_range = requirement["version_range"]
        if not version_satisfies(dep.manifest.version, version_range):
            raise PluginRegistryError(
                f"'{plugin_id}' requires '{dep_id}' {version_range}, but "
                f"'{dep_id}' is {dep.manifest.version}."
            )

    def stop(self, plugin_id: str) -> None:
        """Stop a plugin, refusing while a started plugin still needs it."""
        record = self._require(plugin_id)
        for other in self._plugins.values():
            if other.state is LifecycleState.STARTED and any(
                req["plugin_id"] == plugin_id for req in other.manifest.requires
            ):
                raise PluginRegistryError(
                    f"Cannot stop '{plugin_id}': '{other.manifest.plugin_id}' is "
                    "started and depends on it."
                )
        self._call_hook(record, "stop")
        record.state = LifecycleState.STOPPED

    def reload(self, plugin_id: str) -> None:
        """Hot-reload a started, reloadable plugin in place."""
        record = self._require(plugin_id)
        if not record.manifest.reloadable:
            raise PluginRegistryError(f"Plugin '{plugin_id}' is not reloadable.")
        if record.state is not LifecycleState.STARTED:
            raise PluginRegistryError(
                f"Only a started plugin can be reloaded; '{plugin_id}' is "
                f"{record.state.value}."
            )
        record.state = LifecycleState.RELOADING
        if hasattr(record.instance, "reload"):
            self._call_hook(record, "reload")
        else:
            self._call_hook(record, "stop")
            self._call_hook(record, "start")
        record.state = LifecycleState.STARTED

    def _call_hook(self, record: _Registered, name: str) -> None:
        hook = getattr(record.instance, name, None)
        if hook is None:
            return
        try:
            hook()
        except Exception as error:  # noqa: BLE001 - a plugin fault must not escape as-is
            record.state = LifecycleState.FAILED
            record.health = "unhealthy"
            record.detail = f"{name} hook raised: {error}"
            raise PluginRegistryError(
                f"Plugin '{record.manifest.plugin_id}' {name} hook failed: {error}"
            ) from error

    # -- introspection ----------------------------------------------------

    def state(self, plugin_id: str) -> LifecycleState:
        return self._require(plugin_id).state

    def health(self, plugin_id: str, now: datetime | None = None, max_age_s: float = 10.0) -> PluginHealth:
        """Take a health snapshot, probing the plugin's own hook if it has one."""
        record = self._require(plugin_id)
        checked_at = now if now is not None else datetime.now(UTC)
        health = record.health
        if record.state is not LifecycleState.FAILED and hasattr(record.instance, "health"):
            health = record.instance.health()
        elif record.state is LifecycleState.STARTED and not hasattr(record.instance, "health"):
            health = "ok"
        return PluginHealth(
            plugin_id=plugin_id,
            lifecycle_state=record.state.value,
            health=health,
            checked_at=checked_at,
            max_age_s=max_age_s,
            detail=record.detail,
        )

    def plugin_ids(self) -> tuple[str, ...]:
        return tuple(self._plugins)

    def _require(self, plugin_id: str) -> _Registered:
        record = self._plugins.get(plugin_id)
        if record is None:
            raise PluginRegistryError(f"Plugin '{plugin_id}' is not registered.")
        return record

    # -- cycle detection --------------------------------------------------

    def _find_cycle(self) -> list[str] | None:
        visited: set[str] = set()
        stack: list[str] = []
        on_stack: set[str] = set()

        def visit(node: str) -> list[str] | None:
            visited.add(node)
            stack.append(node)
            on_stack.add(node)
            record = self._plugins.get(node)
            if record is not None:
                for req in record.manifest.requires:
                    nxt = req["plugin_id"]
                    if nxt not in self._plugins:
                        continue  # a not-yet-registered dep cannot be part of a cycle
                    if nxt in on_stack:
                        return stack[stack.index(nxt):] + [nxt]
                    if nxt not in visited:
                        found = visit(nxt)
                        if found is not None:
                            return found
            stack.pop()
            on_stack.discard(node)
            return None

        for plugin_id in self._plugins:
            if plugin_id not in visited:
                found = visit(plugin_id)
                if found is not None:
                    return found
        return None


def version_satisfies(version: str, version_range: str) -> bool:
    """Whether a semver ``major.minor.patch`` meets a comma-joined constraint.

    The grammar is deliberately small for v0.1: comma-separated comparators, all
    of which must hold (logical AND), each one of ``>=`` ``>`` ``<=`` ``<`` ``==``
    followed by a ``major.minor.patch`` version, e.g. ``">=1.0.0,<2.0.0"``. An
    unparseable constraint is not quietly treated as satisfied: it raises.
    """
    target = _parse_version(version)
    clauses = [clause.strip() for clause in version_range.split(",") if clause.strip()]
    if not clauses:
        raise PluginRegistryError(f"Empty version range '{version_range}'.")
    for clause in clauses:
        operator, bound_text = _split_clause(clause)
        bound = _parse_version(bound_text)
        if not _COMPARATORS[operator](target, bound):
            return False
    return True


_COMPARATORS = {
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
    ">": lambda a, b: a > b,
    "<": lambda a, b: a < b,
}


def _split_clause(clause: str) -> tuple[str, str]:
    for operator in (">=", "<=", "==", ">", "<"):
        if clause.startswith(operator):
            return operator, clause[len(operator):].strip()
    raise PluginRegistryError(f"Version clause '{clause}' has no known comparator.")


def _parse_version(text: str) -> tuple[int, int, int]:
    parts = text.split(".")
    if len(parts) != 3:
        raise PluginRegistryError(f"Version '{text}' is not major.minor.patch.")
    try:
        return (int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError as error:
        raise PluginRegistryError(f"Version '{text}' is not numeric.") from error
