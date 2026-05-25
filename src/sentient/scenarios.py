from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import AgentEvent, DecisionType, EventType
from .validation import load_json, validate_scenario_dict


@dataclass(frozen=True)
class PolicyScenario:
    name: str
    event: AgentEvent
    expected_decision: DecisionType

    @classmethod
    def from_file(cls, path: str | Path) -> "PolicyScenario":
        data = load_json(path)
        errors = validate_scenario_dict(data)
        if errors:
            raise ValueError(f"{path}: " + "; ".join(errors))
        event = data["event"]
        return cls(
            name=data["name"],
            event=AgentEvent(
                agent_id=event["agent_id"],
                task_id=event.get("task_id"),
                event_type=EventType(event["event_type"]),
                content=event["content"],
                metadata=event.get("metadata", {}),
            ),
            expected_decision=DecisionType(data["expected_decision"]),
        )


def scenario_files(path: str | Path) -> list[Path]:
    root = Path(path)
    if root.is_file():
        return [root]
    return sorted(root.glob("*.json"))

