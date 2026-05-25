# Customer Integration Guide

This guide explains how a company connects production AI agents to Sentient. The recommended first integration is the runtime tool proxy in shadow mode, because it gives safety visibility without disrupting production traffic.

## Integration Options

Choose one path for the pilot.

| Path | Best For | Production Shape |
| --- | --- | --- |
| Runtime tool proxy | Most first customers | Agent calls Sentient instead of calling tools directly |
| Decision API | Existing gateway or orchestration layer | Agent/runtime asks Sentient for a decision, then customer code enforces it |
| Python SDK wrapper | Python agents or internal demos | Wrap each sensitive tool function with `supervisor.guard_tool(...)` |

For first customers, prefer the runtime tool proxy.

## What Sentient Needs From Each Agent Action

Every monitored action should include enough context for Sentient to decide whether the action is safe.

Required:

- `agent_id`: stable ID for the agent instance or agent type
- `tool_name`: tool/action the agent wants to run
- `tool_args`: arguments the agent wants to send to the tool
- `event_type`: usually `tool_call` for actions

Strongly recommended:

- `task_id`: ticket, workflow, job, session, or case ID
- `agent_role`: role used for policy checks
- `environment`: `dev`, `staging`, or `production`
- `tenant_id`: customer/workspace/account boundary
- `user_id` or `customer_id`: downstream subject, if allowed by the customer's privacy rules
- `claims`, `sources`, and `artifacts`: for final answers or research-like outputs

Example tool-call context:

```json
{
  "agent_id": "support-agent-7",
  "task_id": "ticket-1842",
  "tool_name": "issue_refund",
  "tool_args": {
    "customer_id": "cust_991",
    "amount": 950
  },
  "metadata": {
    "agent_role": "support_manager",
    "environment": "production"
  }
}
```

## Path 1: Runtime Tool Proxy

The agent stops calling sensitive tools directly. Instead, it calls Sentient:

```text
agent -> Sentient /v1/tool-call -> upstream production tool
```

Start Sentient in shadow mode:

```bash
PYTHONPATH=src python3 -m sentient.cli serve \
  --policy examples/first_customer/customer_policy.json \
  --tool-routes examples/first_customer/tool_routes.json \
  --context-store demo_output/first_customer/context \
  --tenant-id first-customer \
  --enforcement-mode shadow
```

Agent request:

```bash
curl -X POST http://127.0.0.1:8080/v1/tool-call \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "support-agent-7",
    "task_id": "ticket-1842",
    "tool_name": "issue_refund",
    "tool_args": {
      "customer_id": "cust_991",
      "amount": 950
    },
    "metadata": {
      "agent_role": "support_manager",
      "environment": "production"
    }
  }'
```

Shadow response for a risky call:

```json
{
  "tool_call": {
    "status": "shadow_executed",
    "decision": {
      "decision_type": "require_human_approval",
      "enforcement_mode": "shadow",
      "enforced": false
    },
    "upstream_response": {}
  }
}
```

Enforce mode response for the same call:

```json
{
  "tool_call": {
    "status": "approval_required",
    "approval_request_id": "req_123",
    "decision": {
      "decision_type": "require_human_approval",
      "enforcement_mode": "enforce",
      "enforced": true
    }
  }
}
```

## Tool Route File

`tool_routes.json` maps a Sentient tool name to the customer's actual tool endpoint.

```json
[
  {
    "tool_name": "issue_refund",
    "url": "https://customer.example.com/tools/issue-refund",
    "method": "POST",
    "forward_mode": "tool_args",
    "headers": {
      "Authorization": "Bearer ${CUSTOMER_TOOL_TOKEN}"
    }
  }
]
```

Route guidance:

- Use `tool_args` when the upstream tool only expects the original tool arguments.
- Use `envelope` when the upstream tool also needs `agent_id`, `task_id`, `tool_name`, and `metadata`.
- Put secrets in environment variables or the customer's secret manager; do not commit real credentials.

## Path 2: Decision API

Use the decision API when the customer already has a gateway or orchestration layer that can enforce Sentient's response.

Start Sentient:

```bash
PYTHONPATH=src python3 -m sentient.cli serve \
  --policy examples/first_customer/customer_policy.json \
  --context-store demo_output/first_customer/context \
  --tenant-id first-customer \
  --enforcement-mode shadow
```

Send an event:

```bash
curl -X POST http://127.0.0.1:8080/v1/decide \
  -H "Content-Type: application/json" \
  -d '{
    "event": {
      "agent_id": "support-agent-7",
      "task_id": "ticket-1842",
      "event_type": "tool_call",
      "content": "Issue refund",
      "metadata": {
        "tool_name": "issue_refund",
        "agent_role": "support_manager",
        "tool_args": {
          "amount": 950
        }
      }
    }
  }'
```

Customer code should enforce:

- `allow`: continue
- `require_human_approval`: pause and route to reviewer in enforce mode
- `block`: do not execute; stop or quarantine the violating agent in enforce mode

In shadow mode, customer code can continue execution while storing the decision for review.

## Path 3: Python SDK Wrapper

Use the SDK when the agent and tool functions run in Python.

```python
from sentient import HumanApprovalRequired, ToolCallBlocked


def issue_refund(customer_id: str, amount: int) -> str:
    return f"Refunded {amount} to {customer_id}"


guarded_issue_refund = supervisor.guard_tool(
    "issue_refund",
    issue_refund,
    default_metadata={
        "agent_role": "support_manager",
        "environment": "production",
    },
)

try:
    result = guarded_issue_refund(
        "cust_991",
        950,
        agent_id="support-agent-7",
        task_id="ticket-1842",
    )
except HumanApprovalRequired as approval:
    request_id = approval.approval_request.request_id
except ToolCallBlocked as blocked:
    reason = blocked.decision.summary
```

In shadow mode, the wrapper logs the risky decision but still runs the tool.

## Company Policy Context

Before a pilot, ingest the customer's policy/SOP/security docs:

```bash
PYTHONPATH=src python3 -m sentient.cli context ingest \
  --tenant-id first-customer \
  --source examples/first_customer/context_docs \
  --store demo_output/first_customer/context \
  --llm-provider ollama \
  --llm-model llama3.2
```

Review draft rules:

```bash
PYTHONPATH=src python3 -m sentient.cli context rules list \
  --tenant-id first-customer \
  --store demo_output/first_customer/context \
  --status draft
```

Activate only reviewed rules:

```bash
PYTHONPATH=src python3 -m sentient.cli context rules activate \
  --tenant-id first-customer \
  --store demo_output/first_customer/context \
  --rule-id <rule_id> \
  --by security@example.com
```

Raw extracted rules remain `draft` and do not affect monitoring until activated.

## Pilot Rollout

1. Connect one workflow through the proxy.
2. Run in `shadow` mode for several days of representative traffic.
3. Export audit records:

```bash
PYTHONPATH=src python3 -m sentient.cli audit export \
  --store logs/audit.jsonl \
  --format csv \
  --enforcement-mode shadow \
  --output demo_output/first_customer/shadow-findings.csv
```

4. Generate the pilot report:

```bash
PYTHONPATH=src python3 -m sentient.cli pilot report \
  --audit logs/audit.jsonl \
  --approvals logs/approvals.jsonl \
  --output demo_output/first_customer/pilot-report.md
```

5. Replay the customer's scenarios:

```bash
PYTHONPATH=src python3 -m sentient.cli eval run \
  --policy examples/first_customer/customer_policy.json \
  --suite examples/first_customer/scenarios \
  --no-llm-brain \
  --output demo_output/first_customer/eval-report.json
```

6. Move selected high-risk tools to `enforce` mode.

## Production Hardening Checklist

- Use API keys or HMAC signatures for Sentient API access.
- Pass `X-Tenant-ID` when running multi-tenant pilots.
- Redact sensitive fields before logs leave the customer's environment.
- Configure alerting for `block` and `require_human_approval`.
- Keep `tool_routes.json` under change review.
- Keep company context docs and activated graph rules versioned.
- Decide fail-open or fail-closed behavior for each critical tool route.

## Acceptance Checklist

The integration is ready for first production enforcement when:

- selected tools no longer bypass Sentient
- shadow logs show real traffic and useful findings
- noisy draft/context rules have been rejected or refined
- active rules cite source policy documents where possible
- scenario evals pass
- approval owners can approve/reject requests
- the customer has signed off on the first enforced tools
