from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


def _load_demo_module():
    examples_dir = str(Path("examples").resolve())
    if examples_dir not in sys.path:
        sys.path.insert(0, examples_dir)
    path = Path("examples/ollama_local_support_demo.py")
    spec = importlib.util.spec_from_file_location("ollama_local_support_demo", path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules["ollama_local_support_demo"] = module
    spec.loader.exec_module(module)
    return module


class FakeOllamaClient:
    def __init__(self, responses: list[dict]):
        self.responses = responses
        self.calls = 0

    def chat(self, messages):
        response = self.responses[self.calls]
        self.calls += 1
        return json.dumps(response)


class OllamaDemoTests(unittest.TestCase):
    def test_ollama_demo_runs_agent_loop_with_sentient_guarded_tools(self) -> None:
        module = _load_demo_module()
        client = FakeOllamaClient(
            [
                {"tool": "read_ticket", "args": {"ticket_id": "ticket-1842"}},
                {"tool": "search_knowledge_base", "args": {"query": "refund duplicate charge"}},
                {
                    "tool": "draft_email",
                    "args": {
                        "customer_id": "cust_991",
                        "body": "We verified the duplicate charge.",
                    },
                },
                {
                    "tool": "send_email",
                    "args": {
                        "customer_id": "cust_991",
                        "body": "Your refund is being reviewed.",
                    },
                },
                {
                    "tool": "issue_refund",
                    "args": {
                        "customer_id": "cust_991",
                        "amount": 950,
                        "reason": "Duplicate annual subscription charge",
                    },
                },
                {"final": "Ticket resolved with approved refund."},
            ]
        )

        with TemporaryDirectory() as temp_dir:
            summary = module.run_ollama_demo(output_dir=temp_dir, client=client)

            self.assertEqual(summary["final_output"], "Ticket resolved with approved refund.")
            self.assertIsNone(summary["interrupted_by"])
            step_types = [step["type"] for step in summary["steps"]]
            self.assertIn("approval_required", step_types)
            self.assertTrue(Path(summary["audit_path"]).exists())
            self.assertTrue(Path(summary["approvals_path"]).exists())

    def test_ollama_demo_records_blocked_tool_call(self) -> None:
        module = _load_demo_module()
        client = FakeOllamaClient(
            [
                {"tool": "export_customer_database", "args": {}},
            ]
        )

        with TemporaryDirectory() as temp_dir:
            summary = module.run_ollama_demo(output_dir=temp_dir, client=client)

            self.assertEqual(summary["interrupted_by"]["type"], "tool_call_blocked")
            self.assertIn("blocked-tool", summary["final_output"])

    def test_normalizes_common_model_argument_aliases(self) -> None:
        module = _load_demo_module()

        self.assertEqual(
            module._normalize_tool_args(
                "send_email",
                {
                    "customer_id": "cust_991",
                    "body_file_path": "draft.txt",
                    "message": "hello",
                },
            ),
            {"customer_id": "cust_991", "body": "hello"},
        )
        self.assertEqual(
            module._normalize_tool_args(
                "issue_refund",
                {"customer_id": "cust_991", "amount": "950"},
            )["amount"],
            950,
        )

    def test_consistency_warning_detects_refund_amount_mismatch(self) -> None:
        module = _load_demo_module()

        warnings = module._consistency_warnings(
            "Refund of $950 processed.",
            [{"amount": 900}],
        )

        self.assertEqual(len(warnings), 1)
        self.assertIn("$900", warnings[0])


if __name__ == "__main__":
    unittest.main()
