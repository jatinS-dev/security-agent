from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from sentient import (
    AgentEvent,
    EnforcementMode,
    EventType,
    FileAuditStore,
    InMemoryAgentController,
    Policy,
    PolicyEngine,
    SecuritySupervisor,
    ToolCallBlocked,
)


class ShadowModeTests(unittest.TestCase):
    def test_supervisor_logs_block_without_stopping_agent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            audit_path = Path(temp_dir) / "audit.jsonl"
            controller = InMemoryAgentController()
            supervisor = SecuritySupervisor(
                policy_engine=PolicyEngine(
                    Policy.from_dict(
                        {
                            "name": "shadow",
                            "version": "1",
                            "allowed_tools": ["deploy_production"],
                            "blocked_tool_names": ["deploy_production"],
                        }
                    )
                ),
                controller=controller,
                enforcement_mode=EnforcementMode.SHADOW,
                audit_store=FileAuditStore(audit_path, redact=False),
            )

            decision = supervisor.observe(
                AgentEvent(
                    agent_id="devops-agent-1",
                    event_type=EventType.TOOL_CALL,
                    content="Deploy production",
                    metadata={"tool_name": "deploy_production"},
                )
            )

            self.assertEqual(decision.decision_type.value, "block")
            self.assertFalse(decision.enforced)
            self.assertEqual(decision.enforcement_mode, EnforcementMode.SHADOW)
            self.assertFalse(controller.is_stopped("devops-agent-1"))

            record = json.loads(audit_path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(record["decision"]["enforcement_mode"], "shadow")
            self.assertFalse(record["decision"]["enforced"])

    def test_guarded_tool_executes_in_shadow_despite_block(self) -> None:
        calls = []
        supervisor = SecuritySupervisor(
            policy_engine=PolicyEngine(
                Policy.from_dict(
                    {
                        "name": "shadow sdk",
                        "version": "1",
                        "allowed_tools": ["dangerous_tool"],
                        "blocked_tool_names": ["dangerous_tool"],
                    }
                )
            ),
            controller=InMemoryAgentController(),
            enforcement_mode=EnforcementMode.SHADOW,
        )
        guarded = supervisor.guard_tool(
            "dangerous_tool",
            lambda: calls.append("executed") or "ok",
        )

        result = guarded(agent_id="agent-1")

        self.assertEqual(result, "ok")
        self.assertEqual(calls, ["executed"])
        self.assertEqual(supervisor.audit_log[0].decision.decision_type.value, "block")
        self.assertFalse(supervisor.audit_log[0].decision.enforced)

    def test_guarded_tool_still_blocks_in_enforce_mode(self) -> None:
        supervisor = SecuritySupervisor(
            policy_engine=PolicyEngine(
                Policy.from_dict(
                    {
                        "name": "enforce sdk",
                        "version": "1",
                        "allowed_tools": ["dangerous_tool"],
                        "blocked_tool_names": ["dangerous_tool"],
                    }
                )
            ),
            controller=InMemoryAgentController(),
        )
        guarded = supervisor.guard_tool("dangerous_tool", lambda: "ok")

        with self.assertRaises(ToolCallBlocked):
            guarded(agent_id="agent-1")


if __name__ == "__main__":
    unittest.main()
