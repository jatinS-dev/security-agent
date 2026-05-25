from __future__ import annotations

from dataclasses import dataclass

from .python import PythonRuntimeAdapter


@dataclass
class CrewAIAdapter(PythonRuntimeAdapter):
    """Dependency-light adapter for CrewAI tool callables."""

