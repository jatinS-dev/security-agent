from __future__ import annotations

import importlib.util
import os
import sys
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


def _load_demo_module():
    path = Path("examples/openai_agents_real_support_demo.py")
    spec = importlib.util.spec_from_file_location("openai_agents_real_support_demo", path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules["openai_agents_real_support_demo"] = module
    spec.loader.exec_module(module)
    return module


class OpenAIAgentsRealDemoTests(unittest.TestCase):
    def test_requires_openai_api_key(self) -> None:
        module = _load_demo_module()
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "OPENAI_API_KEY"):
                module.run_openai_demo(output_dir="/tmp/not-used")

    def test_model_quota_error_is_reported_without_traceback(self) -> None:
        module = _load_demo_module()

        class QuotaError(Exception):
            status_code = 429
            code = "insufficient_quota"

        message = module._format_model_error(QuotaError("quota"))

        self.assertIn("insufficient_quota", message)
        self.assertIn("before any tool calls", message)

    def test_build_agent_uses_openai_agent_and_guarded_function_tools(self) -> None:
        module = _load_demo_module()
        fake_agents = types.ModuleType("agents")
        function_tool_calls = []

        class FakeAgent:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                self.tools = kwargs["tools"]

        def fake_function_tool(**kwargs):
            function_tool_calls.append(kwargs)

            def decorate(func):
                setattr(func, "tool_kwargs", kwargs)
                return func

            return decorate

        fake_agents.Agent = FakeAgent
        fake_agents.function_tool = fake_function_tool

        with TemporaryDirectory() as temp_dir, patch.dict(
            sys.modules,
            {"agents": fake_agents},
        ):
            supervisor = module.build_supervisor(Path(temp_dir))
            sandbox = module.SupportSandbox(Path(temp_dir))
            agent = module.build_agent(supervisor, sandbox, "gpt-test")

            self.assertEqual(agent.kwargs["model"], "gpt-test")
            self.assertEqual(len(agent.tools), 6)
            self.assertTrue(
                all(call["failure_error_function"] is None for call in function_tool_calls)
            )

            self.assertIn("ticket-1842", agent.tools[0]("ticket-1842"))


if __name__ == "__main__":
    unittest.main()
