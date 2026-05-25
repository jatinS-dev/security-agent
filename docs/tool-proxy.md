# Runtime Tool Proxy

The runtime tool proxy lets Sentient sit between production AI agents and production tools.

Instead of an agent calling a sensitive tool directly, it calls:

```text
POST /v1/tool-call
```

Sentient then:

- builds a tool-call event
- checks deterministic policy, active context rules, and the LLM brain
- blocks unsafe calls
- creates approval requests for risky calls
- forwards only allowed or approved calls to the configured upstream tool
- writes the decision to the audit log

## Shadow Mode

For first customer pilots, run the proxy in shadow mode. Sentient still evaluates the real policy decision, but it forwards the call and records what would have happened.

```bash
PYTHONPATH=src python3 -m sentient.cli serve \
  --policy policies/examples/customer_support_policy.json \
  --tool-routes examples/tool_routes.example.json \
  --enforcement-mode shadow
```

When a risky call is forwarded in shadow mode, the proxy returns:

```json
{
  "tool_call": {
    "status": "shadow_executed",
    "decision": {
      "decision_type": "block",
      "enforcement_mode": "shadow",
      "enforced": false
    }
  }
}
```

## Start The Proxy API

Run the local demo:

```bash
PYTHONPATH=src python3 examples/tool_proxy_demo.py
```

Create a tool route file like `examples/tool_routes.example.json`, then run:

```bash
PYTHONPATH=src python3 -m sentient.cli serve \
  --policy policies/examples/customer_support_policy.json \
  --tool-routes examples/tool_routes.example.json
```

For context-aware production monitoring, include the company context graph:

```bash
PYTHONPATH=src python3 -m sentient.cli serve \
  --policy policies/examples/customer_support_policy.json \
  --context-store demo_output/context_graph/context \
  --tenant-id acme \
  --tool-routes examples/tool_routes.example.json
```

## Agent Request

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
      "agent_role": "support_manager"
    }
  }'
```

Response statuses:

- `executed`: Sentient allowed the call and forwarded it upstream.
- `shadow_executed`: Sentient forwarded the call in shadow mode, even though it would have blocked or approval-gated in enforce mode.
- `approval_required`: Sentient created an approval request and did not forward the call.
- `blocked`: Sentient blocked the call and did not forward it.

## Execute After Approval

After a reviewer approves the request:

```bash
PYTHONPATH=src python3 -m sentient.cli approvals approve \
  <request_id> \
  --reviewer finance@example.com
```

Execute the approved tool call:

```bash
curl -X POST http://127.0.0.1:8080/v1/tool-call/execute-approved \
  -H "Content-Type: application/json" \
  -d '{"request_id": "<request_id>"}'
```

Sentient verifies the approval is approved, forwards the saved tool arguments, and marks the approval as `executed`.

## Route Config

Routes can use either `envelope` mode or `tool_args` mode.

`envelope` forwards:

```json
{
  "agent_id": "support-agent-7",
  "task_id": "ticket-1842",
  "tool_name": "issue_refund",
  "tool_args": {"amount": 950},
  "metadata": {"agent_role": "support_manager"}
}
```

`tool_args` forwards only:

```json
{"amount": 950}
```
