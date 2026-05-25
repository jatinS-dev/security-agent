from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import DecisionType, EventType, Severity


def load_json(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object.")
    return data


def validate_policy_dict(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    _require_string(data, "name", errors)
    _require_string(data, "version", errors)

    for key in ("allowed_tools", "blocked_tool_names", "tools_requiring_approval"):
        _optional_string_list(data, key, errors)

    _optional_number_map(data, "max_autonomous_amounts", errors)
    for key in (
        "tool_role_requirements",
        "approval_tool_environments",
        "blocked_tool_environments",
        "approval_routes",
    ):
        _optional_string_list_map(data, key, errors)
    if "approval_expiration_minutes" in data and not isinstance(
        data["approval_expiration_minutes"],
        int,
    ):
        errors.append("approval_expiration_minutes must be an integer.")

    patterns = data.get("blocked_content_patterns", [])
    if not isinstance(patterns, list):
        errors.append("blocked_content_patterns must be a list.")
    else:
        for index, pattern in enumerate(patterns):
            if not isinstance(pattern, dict):
                errors.append(f"blocked_content_patterns[{index}] must be an object.")
                continue
            _require_string(pattern, "id", errors, f"blocked_content_patterns[{index}]")
            _require_string(
                pattern,
                "description",
                errors,
                f"blocked_content_patterns[{index}]",
            )
            _require_string(
                pattern,
                "pattern",
                errors,
                f"blocked_content_patterns[{index}]",
            )
            if pattern.get("severity", Severity.HIGH.value) not in {
                severity.value for severity in Severity
            }:
                errors.append(
                    f"blocked_content_patterns[{index}].severity is invalid."
                )

    for key in ("completion_requires_artifacts", "factual_claims_require_sources"):
        if key in data and not isinstance(data[key], bool):
            errors.append(f"{key} must be a boolean.")
    return errors


def validate_scenario_dict(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    _require_string(data, "name", errors)
    event = data.get("event")
    if not isinstance(event, dict):
        errors.append("event must be an object.")
    else:
        _require_string(event, "agent_id", errors, "event")
        _require_string(event, "event_type", errors, "event")
        if event.get("event_type") not in {event_type.value for event_type in EventType}:
            errors.append("event.event_type is invalid.")
        _require_string(event, "content", errors, "event")
        if "metadata" in event and not isinstance(event["metadata"], dict):
            errors.append("event.metadata must be an object.")

    expected = data.get("expected_decision")
    if expected not in {decision.value for decision in DecisionType}:
        errors.append("expected_decision must be allow, block, or require_human_approval.")
    return errors


def _require_string(
    data: dict[str, Any],
    key: str,
    errors: list[str],
    prefix: str | None = None,
) -> None:
    label = f"{prefix}.{key}" if prefix else key
    if not isinstance(data.get(key), str) or not data.get(key):
        errors.append(f"{label} must be a non-empty string.")


def _optional_string_list(data: dict[str, Any], key: str, errors: list[str]) -> None:
    value = data.get(key)
    if value is None:
        return
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        errors.append(f"{key} must be a list of strings.")


def _optional_number_map(data: dict[str, Any], key: str, errors: list[str]) -> None:
    value = data.get(key)
    if value is None:
        return
    if not isinstance(value, dict):
        errors.append(f"{key} must be an object.")
        return
    for map_key, map_value in value.items():
        if not isinstance(map_key, str) or not isinstance(map_value, (int, float)):
            errors.append(f"{key} must map strings to numbers.")
            return


def _optional_string_list_map(data: dict[str, Any], key: str, errors: list[str]) -> None:
    value = data.get(key)
    if value is None:
        return
    if not isinstance(value, dict):
        errors.append(f"{key} must be an object.")
        return
    for map_key, map_value in value.items():
        if not isinstance(map_key, str) or not isinstance(map_value, list):
            errors.append(f"{key} must map strings to lists of strings.")
            return
        if not all(isinstance(item, str) for item in map_value):
            errors.append(f"{key} must map strings to lists of strings.")
            return
