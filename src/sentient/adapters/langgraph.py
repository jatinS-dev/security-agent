from __future__ import annotations

from dataclasses import dataclass

from .python import PythonRuntimeAdapter


@dataclass
class LangGraphAdapter(PythonRuntimeAdapter):
    """Dependency-light adapter for LangGraph tool callables."""

