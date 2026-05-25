from __future__ import annotations

import json
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from sentient.real_demo import main, run_demo


class RealDemoTests(unittest.TestCase):
    def test_real_demo_runs_end_to_end_and_writes_artifacts(self) -> None:
        with TemporaryDirectory() as temp_dir:
            summary = run_demo(temp_dir)

            outcomes = {step.name: step.outcome for step in summary.steps}
            self.assertEqual(outcomes["read ticket"], "allowed")
            self.assertEqual(outcomes["send email"], "approval_required")
            self.assertEqual(outcomes["send email after approval"], "executed")
            self.assertEqual(outcomes["issue high-value refund"], "approval_required")
            self.assertEqual(outcomes["issue refund after approval"], "executed")
            self.assertEqual(
                outcomes["export customer database"],
                "blocked_and_agent_stopped",
            )
            self.assertEqual(outcomes["unsupported factual claim"], "block")

            audit_path = Path(summary.audit_path)
            approvals_path = Path(summary.approvals_path)
            self.assertTrue(audit_path.exists())
            self.assertTrue(approvals_path.exists())
            self.assertTrue((Path(temp_dir) / "demo-summary.json").exists())

            audit_lines = [
                json.loads(line)
                for line in audit_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            decision_types = [
                line["decision"]["decision_type"]
                for line in audit_lines
            ]
            self.assertIn("require_human_approval", decision_types)
            self.assertIn("block", decision_types)
            self.assertEqual(
                summary.agent_states["support-agent-data-export"],
                "stopped",
            )

    def test_real_demo_cli_json_output(self) -> None:
        with TemporaryDirectory() as temp_dir:
            stdout = StringIO()
            with redirect_stdout(stdout):
                self.assertEqual(main(["--output-dir", temp_dir, "--json"]), 0)
            self.assertIn("agent_states", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
