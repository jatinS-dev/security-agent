from __future__ import annotations

from dataclasses import dataclass

from .python import PythonRuntimeAdapter


@dataclass
class AutoGenAdapter(PythonRuntimeAdapter):
    """Dependency-light adapter for AutoGen tool callables."""

