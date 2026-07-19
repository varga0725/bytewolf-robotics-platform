"""Local, session-scoped user-memory store for the dashboard control plane.

The Pi post-turn hook proposes facts, but this module is the user-facing
control surface: it exposes only admitted facts and lets the session owner
correct or erase them.  It has no mission or flight dependencies.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from pathlib import Path
import re
from typing import Any


ALLOWED_CATEGORIES = frozenset({"name", "preference", "place_label", "relationship"})
MAX_FACT_CHARS = 240
_SENSITIVE = re.compile(
    r"\b(api\s*key|api[-_ ]?kulcs\w*|token|jelsz[oó]|password|secret|titok|"
    r"bankk[aá]rtya|credit\s*card|e-?mail|email|telefonsz[aá]m|phone)\b|"
    r"\b(?:utca|street|road|avenue)\b|\b\d{12,}\b|"
    r"\b[\w.+-]+@[\w.-]+\.[a-z]{2,}\b|\+?\d[\d\s()-]{7,}\d",
    re.IGNORECASE,
)


class MemoryStoreError(ValueError):
    """A dashboard memory update did not meet the durable-memory contract."""


def list_memory(memory_dir: Path, session_id: str) -> dict[str, list[dict[str, str]]]:
    """Return a sanitized, immutable snapshot of one session's admitted facts."""
    facts = _load_facts(memory_dir, session_id)
    return {"facts": [_public_fact(fact, index) for index, fact in enumerate(facts)]}


def update_memory_fact(
    memory_dir: Path, session_id: str, fact_id: str, *, category: str, fact: str
) -> dict[str, list[dict[str, str]]]:
    admitted_category, admitted_fact = _admit(category, fact)
    facts = _load_facts(memory_dir, session_id)
    replacement = False
    next_facts: list[dict[str, Any]] = []
    for index, item in enumerate(facts):
        if _fact_id(item, index) == fact_id:
            replacement = True
            next_facts.append({
                **item,
                "category": admitted_category,
                "fact": admitted_fact,
                "corrected_by_user": True,
            })
        else:
            next_facts.append(dict(item))
    if not replacement:
        raise KeyError("Memory fact was not found.")
    _write_facts(memory_dir, session_id, next_facts)
    return {"facts": [_public_fact(item, index) for index, item in enumerate(next_facts)]}


def delete_memory_fact(memory_dir: Path, session_id: str, fact_id: str) -> dict[str, list[dict[str, str]]]:
    facts = _load_facts(memory_dir, session_id)
    next_facts = [dict(item) for index, item in enumerate(facts) if _fact_id(item, index) != fact_id]
    if len(next_facts) == len(facts):
        raise KeyError("Memory fact was not found.")
    _write_facts(memory_dir, session_id, next_facts)
    return {"facts": [_public_fact(item, index) for index, item in enumerate(next_facts)]}


def _load_facts(memory_dir: Path, session_id: str) -> list[dict[str, Any]]:
    path = memory_dir / f"{session_id}.json"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return []
    raw_facts = document.get("facts") if isinstance(document, Mapping) else None
    if not isinstance(raw_facts, list):
        return []
    return [dict(item) for item in raw_facts if _is_admitted_fact(item)]


def _write_facts(memory_dir: Path, session_id: str, facts: list[dict[str, Any]]) -> None:
    memory_dir.mkdir(parents=True, exist_ok=True)
    destination = memory_dir / f"{session_id}.json"
    temporary = destination.with_suffix(".json.tmp")
    temporary.write_text(json.dumps({"facts": facts}, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(destination)


def _is_admitted_fact(item: object) -> bool:
    if not isinstance(item, Mapping):
        return False
    try:
        _admit(str(item.get("category", "")), str(item.get("fact", "")))
    except MemoryStoreError:
        return False
    return True


def _admit(category: str, fact: str) -> tuple[str, str]:
    compact = fact.strip()
    compact = re.sub(r"\s+", " ", compact)
    if category not in ALLOWED_CATEGORIES or not compact or len(compact) > MAX_FACT_CHARS or _SENSITIVE.search(compact):
        raise MemoryStoreError("Memory fact is invalid or sensitive.")
    return category, compact


def _fact_id(item: Mapping[str, Any], index: int) -> str:
    identifier = item.get("id")
    if isinstance(identifier, str) and identifier:
        return identifier
    digest = hashlib.sha256(
        f"{item.get('category', '')}\0{item.get('fact', '')}\0{item.get('recorded_at', '')}\0{index}".encode()
    ).hexdigest()[:20]
    return f"legacy-{digest}"


def _public_fact(item: Mapping[str, Any], index: int) -> dict[str, str]:
    return {
        "id": _fact_id(item, index),
        "category": str(item["category"]),
        "fact": str(item["fact"]),
        "recorded_at": str(item.get("recorded_at", "")),
    }
