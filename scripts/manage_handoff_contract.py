#!/usr/bin/env python3
"""Resolve and validate the installed manage-article-knowledge handoff contract."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Mapping


class ManagedHandoffError(ValueError):
    """The managed Faithfulness request is missing or contract-incompatible."""


def candidate_skill_roots(explicit: Path | None = None) -> list[Path]:
    candidates: list[Path] = []
    if explicit:
        candidates.append(explicit)
    configured = os.environ.get("MANAGE_ARTICLE_KNOWLEDGE_SKILL", "").strip()
    if configured:
        candidates.append(Path(configured))
    home = Path.home()
    candidates.extend(
        [
            home / ".agents" / "skills" / "manage-article-knowledge",
            home / ".codex" / "skills" / "manage-article-knowledge",
            # In a source checkout, the repository container may itself be
            # the installed root (GitHub layout) or may contain the versioned
            # skill directory (maintenance layout). The normalization below
            # handles both without assuming a drive letter.
            Path(__file__).resolve().parents[2] / "manage-article-knowledge",
        ]
    )
    # Support both a GitHub skill root and the maintenance repository's
    # versioned child directory. An explicit SKILL.md path is also accepted.
    normalized: list[Path] = []
    for candidate in candidates:
        candidate = candidate.expanduser()
        if candidate.name.lower() == "skill.md":
            candidate = candidate.parent
        normalized.append(candidate)
        normalized.append(candidate / "manage-article-knowledge-v0.6")

    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in normalized:
        key = str(candidate.resolve()).lower()
        if key not in seen:
            seen.add(key)
            unique.append(candidate.resolve())
    return unique


def locate_contract(explicit_skill_root: Path | None = None) -> Path:
    checked: list[str] = []
    for root in candidate_skill_roots(explicit_skill_root):
        path = root / "references" / "handoff-contract.json"
        checked.append(str(path))
        skill_file = root / "SKILL.md"
        if path.is_file() and skill_file.is_file():
            skill_text = skill_file.read_text(encoding="utf-8-sig", errors="replace")
            if re.search(r"(?m)^name:\s*manage-article-knowledge\s*$", skill_text):
                return path
    raise ManagedHandoffError(
        "manage-article-knowledge handoff contract not found; checked: " + "; ".join(checked)
    )


def load_contract(explicit_skill_root: Path | None = None) -> tuple[Path, dict[str, Any]]:
    path = locate_contract(explicit_skill_root)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ManagedHandoffError(f"invalid manage handoff contract JSON: {path}") from exc
    required = {"contract_id", "handoff_contract_version", "compatible_versions", "faithfulness_executor", "events"}
    missing = sorted(required - set(value)) if isinstance(value, dict) else sorted(required)
    if missing:
        raise ManagedHandoffError(f"manage handoff contract missing fields: {', '.join(missing)}")
    executor = value.get("faithfulness_executor", {})
    if executor.get("skill_name") != "deepeval-article-audit":
        raise ManagedHandoffError(
            "installed manage contract does not bind Faithfulness to deepeval-article-audit"
        )
    return path, value


def validate_version(received: str, contract: Mapping[str, Any]) -> str:
    received = str(received or "").strip()
    expected = str(contract.get("handoff_contract_version", ""))
    compatible = {str(item) for item in contract.get("compatible_versions", [])}
    if not received:
        raise ManagedHandoffError(f"missing handoff_contract_version; expected {expected}")
    if received not in compatible:
        raise ManagedHandoffError(
            "unsupported_contract_version: "
            f"expected one of {sorted(compatible)}, received {received}"
        )
    return received


def validate_event(event: str, payload: Mapping[str, Any], contract: Mapping[str, Any]) -> None:
    event_contract = contract.get("events", {}).get(event)
    if not isinstance(event_contract, dict):
        raise ManagedHandoffError(f"installed manage contract does not define {event}")
    if str(payload.get("handoff_event", "")).strip() != event:
        raise ManagedHandoffError(f"handoff_event must be {event}")
    validate_version(str(payload.get("handoff_contract_version", "")), contract)
    missing = [
        field for field in event_contract.get("required_fields", [])
        if payload.get(field) is None or str(payload.get(field)).strip() == ""
    ]
    if missing:
        raise ManagedHandoffError(f"{event} missing required fields: {', '.join(missing)}")
