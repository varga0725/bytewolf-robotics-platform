"""Static guardrail for the Vision observation-only dependency boundary."""

from __future__ import annotations

import ast
from pathlib import Path


_FORBIDDEN_SEGMENTS = frozenset(("mavsdk", "px4", "flight_control", "actuator", "command"))
_FORBIDDEN_PREFIXES = ("brain.mission", "brain.safety", "brain.adapters.mavsdk_adapter")


def forbidden_vision_imports(root: Path) -> tuple[str, ...]:
    """Return flight-control imports found below ``root``, in stable order."""
    violations: list[str] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        importlib_aliases, import_module_aliases = _import_aliases(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                modules = (node.module or "",)
            else:
                modules = ()
            for module in modules:
                if _is_forbidden(module):
                    violations.append(f"{path.relative_to(root)} imports {module}")
            if isinstance(node, ast.Call):
                target = _dynamic_import_target(node, importlib_aliases, import_module_aliases)
                if target is None:
                    continue
                if target == "<nonliteral>":
                    violations.append(f"{path.relative_to(root)} uses a nonliteral dynamic import")
                elif _is_forbidden(target):
                    violations.append(f"{path.relative_to(root)} dynamically imports {target}")
    return tuple(violations)


def _is_forbidden(module: str) -> bool:
    normalized = module.lower()
    segments = frozenset(normalized.split("."))
    return normalized.startswith(_FORBIDDEN_PREFIXES) or bool(segments & _FORBIDDEN_SEGMENTS)


def _import_aliases(tree: ast.AST) -> tuple[frozenset[str], frozenset[str]]:
    importlib_aliases = {"importlib"}
    import_module_aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "importlib":
                    importlib_aliases.add(alias.asname or "importlib")
        elif isinstance(node, ast.ImportFrom) and node.module == "importlib":
            for alias in node.names:
                if alias.name == "import_module":
                    import_module_aliases.add(alias.asname or alias.name)
    _follow_assigned_aliases(tree, importlib_aliases, import_module_aliases)
    return frozenset(importlib_aliases), frozenset(import_module_aliases)


def _follow_assigned_aliases(
    tree: ast.AST, importlib_aliases: set[str], import_module_aliases: set[str]
) -> None:
    """Track importers rebound to another name.

    ``loader = import_module`` (or ``__import__``, or ``importlib.import_module``)
    reaches exactly the same modules as a direct call, so a guard that only knows
    import statements can be walked straight past. Rebinding can chain, so this
    repeats until nothing new is learned.
    """
    import_module_aliases.add("__import__")
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign) or not isinstance(node.value, (ast.Name, ast.Attribute)):
                continue
            value = node.value
            is_importer = (
                isinstance(value, ast.Name) and value.id in import_module_aliases
            ) or (
                isinstance(value, ast.Attribute)
                and value.attr == "import_module"
                and isinstance(value.value, ast.Name)
                and value.value.id in importlib_aliases
            )
            is_importlib = isinstance(value, ast.Name) and value.id in importlib_aliases
            for target in node.targets:
                if not isinstance(target, ast.Name):
                    continue
                if is_importer and target.id not in import_module_aliases:
                    import_module_aliases.add(target.id)
                    changed = True
                if is_importlib and target.id not in importlib_aliases:
                    importlib_aliases.add(target.id)
                    changed = True


def _dynamic_import_target(
    node: ast.Call, importlib_aliases: frozenset[str], import_module_aliases: frozenset[str]
) -> str | None:
    function = node.func
    is_import_module_attribute = (
        isinstance(function, ast.Attribute)
        and function.attr == "import_module"
        and isinstance(function.value, ast.Name)
        and function.value.id in importlib_aliases
    )
    # import_module_aliases carries __import__ and every name rebound to an
    # importer, so a call through an alias is caught like a direct one.
    is_import_module_name = isinstance(function, ast.Name) and function.id in import_module_aliases
    if not (is_import_module_attribute or is_import_module_name):
        return None
    if not node.args or not isinstance(node.args[0], ast.Constant) or not isinstance(node.args[0].value, str):
        return "<nonliteral>"
    return node.args[0].value
