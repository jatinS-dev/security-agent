from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .validation import load_json


@dataclass(frozen=True)
class AgentProfile:
    agent_id: str
    role: str
    owner: str | None = None
    runtime: str | None = None
    risk_level: str | None = None
    allowed_tasks: frozenset[str] = frozenset()


class AgentRegistry(Protocol):
    def get(self, agent_id: str) -> AgentProfile | None:
        raise NotImplementedError


@dataclass
class InMemoryAgentRegistry:
    profiles: dict[str, AgentProfile] = field(default_factory=dict)

    def get(self, agent_id: str) -> AgentProfile | None:
        return self.profiles.get(agent_id)

    def register(self, profile: AgentProfile) -> None:
        self.profiles[profile.agent_id] = profile


class FileAgentRegistry(InMemoryAgentRegistry):
    @classmethod
    def from_file(cls, path: str | Path) -> "FileAgentRegistry":
        data = load_json(path)
        raw_agents = data.get("agents", [])
        if not isinstance(raw_agents, list):
            raise ValueError("agents must be a list.")
        registry = cls()
        for raw_agent in raw_agents:
            if not isinstance(raw_agent, dict):
                raise ValueError("agents must contain objects.")
            registry.register(_profile_from_dict(raw_agent))
        return registry


def _profile_from_dict(data: dict[str, Any]) -> AgentProfile:
    agent_id = data.get("agent_id")
    role = data.get("role")
    if not isinstance(agent_id, str) or not agent_id:
        raise ValueError("agent_id must be a non-empty string.")
    if not isinstance(role, str) or not role:
        raise ValueError(f"role must be a non-empty string for {agent_id}.")
    allowed_tasks = data.get("allowed_tasks", [])
    if not isinstance(allowed_tasks, list) or not all(
        isinstance(item, str) for item in allowed_tasks
    ):
        raise ValueError(f"allowed_tasks must be a list of strings for {agent_id}.")
    return AgentProfile(
        agent_id=agent_id,
        role=role,
        owner=data.get("owner"),
        runtime=data.get("runtime"),
        risk_level=data.get("risk_level"),
        allowed_tasks=frozenset(allowed_tasks),
    )

