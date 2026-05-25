from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from .models import AgentState


class AgentController(ABC):
    """Adapter used by the supervisor to stop only the violating agent."""

    @abstractmethod
    def stop_agent(self, agent_id: str, reason: str) -> None:
        raise NotImplementedError

    def pause_agent(self, agent_id: str, reason: str) -> None:
        raise NotImplementedError

    def resume_agent(self, agent_id: str, reason: str) -> None:
        raise NotImplementedError


@dataclass
class InMemoryAgentController(AgentController):
    states: dict[str, AgentState] = field(default_factory=dict)
    stop_reasons: dict[str, str] = field(default_factory=dict)
    pause_reasons: dict[str, str] = field(default_factory=dict)

    def register(self, agent_id: str) -> None:
        self.states.setdefault(agent_id, AgentState.RUNNING)

    def stop_agent(self, agent_id: str, reason: str) -> None:
        self.states[agent_id] = AgentState.STOPPED
        self.stop_reasons[agent_id] = reason

    def pause_agent(self, agent_id: str, reason: str) -> None:
        self.states[agent_id] = AgentState.PAUSED
        self.pause_reasons[agent_id] = reason

    def resume_agent(self, agent_id: str, reason: str = "") -> None:
        self.states[agent_id] = AgentState.RUNNING
        if reason:
            self.pause_reasons[agent_id] = reason

    def is_stopped(self, agent_id: str) -> bool:
        return self.states.get(agent_id) == AgentState.STOPPED

    def is_paused(self, agent_id: str) -> bool:
        return self.states.get(agent_id) == AgentState.PAUSED
