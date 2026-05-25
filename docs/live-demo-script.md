# Sentient Live Demo Script

Use this while running:

```bash
DEMO_PAUSE=1 ./scripts/live_demo.sh
```

By default, each run writes to a fresh timestamped directory under `demo_output/live_demo/`. The script prints the exact artifact paths at the end.

If Ollama is already running, the demo uses a real local LLM agent. If Ollama is not running, the script falls back to the deterministic risk showcase so you can still present without getting stuck.

To force the real Ollama path:

```bash
REQUIRE_OLLAMA=1 DEMO_PAUSE=1 ./scripts/live_demo.sh
```

## Opening

"Sentient is a safety layer for production AI agents. Companies are starting to let agents send emails, issue refunds, access customer data, deploy code, and call internal tools. The problem is that once an agent has tool access, there is often no independent supervisor watching whether each action is allowed.

Sentient sits between AI agents and the tools they use. It evaluates actions against company policy, company context, deterministic rules, and an LLM risk brain. It can run in shadow mode first, then move selected tools into enforcement."

## 1. Customer Policy Context

Command run by the script:

```bash
PYTHONPATH=src python3 examples/context_graph_demo.py \
  --output-dir demo_output/live_demo/context_graph
```

Say:

"First, the customer gives Sentient their policies, SOPs, approval rules, and security docs. Sentient ingests those documents into a local context graph.

The important product choice is that extracted rules are not automatically enforced. They start as draft rules. A human reviews and activates the ones the company trusts. That keeps the system safe and auditable."

Point out:

- ingested documents
- draft rules
- activated reviewed rules
- decisions with source citations

Transition:

"Now that Sentient understands company rules, let us put it in front of an agent that tries risky things."

## 2. Risky Agent Behavior

Command run by the script when Ollama is available:

```bash
PYTHONPATH=src python3 examples/ollama_risk_agent_demo.py \
  --model llama3.2 \
  --output-dir demo_output/live_demo/risk_agent
```

Say:

"This is a real local AI agent using Ollama. We give it risky instructions like exporting customer data, leaking secrets, leaking payment card data, making an oversized refund, and making unsupported claims.

Sentient observes the agent's intended action before it becomes a production side effect."

Point out the risky cases:

- prompt injection leading to customer database export
- secret leakage
- payment card leakage
- large unauthorized refund
- unsupported customer-history claim

If the script falls back:

"For this run, Ollama is not running, so the script is using the deterministic risk showcase. The safety path is the same: Sentient receives the same kind of agent events and applies the same policy decisions."

## 3. Audit Trail

Command run by the script:

```bash
PYTHONPATH=src python3 -m sentient.cli audit tail \
  --store demo_output/live_demo/risk_agent/audit.jsonl \
  --lines 20
```

Say:

"Every decision is written to an audit log. This matters for enterprise adoption because the security team needs evidence: what happened, which agent did it, what rule matched, and whether Sentient blocked or escalated it."

Explain the columns:

- timestamp
- agent ID
- event type
- decision
- reason

Good line to use:

"This is the bridge from a cool demo to a security product: we are not just blocking, we are creating evidence."

## 4. Shadow Mode Tool Proxy

Before approval queue, the script runs the runtime tool proxy demo:

```bash
PYTHONPATH=src python3 examples/tool_proxy_demo.py
```

Say:

"This is the first-customer integration path. The production agent calls Sentient instead of calling the tool directly. Sentient can allow, block, require approval, or in shadow mode forward the call while recording what would have happened.

This is important because the first customer does not need to risk production disruption on day one."

Point out:

- `read_ticket` is executed
- `issue_refund` requires approval
- `export_customer_database` is blocked
- in shadow mode, `export_customer_database` becomes `shadow_executed`

## 5. Approval Queue

Command run by the script:

```bash
PYTHONPATH=src python3 -m sentient.cli approvals list \
  --store demo_output/live_demo/risk_agent/approvals.jsonl \
  --all
```

Say:

"Not every risky action should be blocked forever. Some actions are legitimate but need approval. For example, a support manager may be allowed to issue a large refund, but not fully autonomously.

Sentient turns those actions into approval requests instead of letting the AI agent execute them alone."

Point out:

- request ID
- agent ID
- tool name
- task ID
- reason approval was required

## 6. Customer Pilot Report

Command run by the script:

```bash
PYTHONPATH=src python3 -m sentient.cli pilot report \
  --audit demo_output/live_demo/risk_agent/audit.jsonl \
  --approvals demo_output/live_demo/risk_agent/approvals.jsonl \
  --output demo_output/live_demo/pilot-report.md
```

Say:

"During the first customer pilot, we do not want to break production. So we run Sentient in shadow mode. It watches real traffic, logs what it would have blocked or approval-gated, and then generates this pilot report.

This is what we hand to the customer after a few days: here are the risky actions your agents attempted, here are the rules that fired, here are the agents and tools creating risk, and here are the first rules we recommend enforcing."

Point out report sections:

- executive summary
- decision breakdown
- top violated rules
- risky tools
- agents with most violations
- sample risky events
- recommended first enforcement rules
- readiness checklist

Strong line:

"The customer does not need to read JSON logs. They get a security outcome."

## 7. Scenario Eval Before Enforcement

Command run by the script:

```bash
PYTHONPATH=src python3 -m sentient.cli eval run \
  --policy policies/default_policy.json \
  --suite scenarios \
  --no-llm-brain \
  --output demo_output/live_demo/eval-report.json
```

Say:

"Before switching from shadow mode to enforcement, we replay a scenario suite. These are known risky behaviors and expected decisions.

This gives the customer confidence that the policy behaves as expected before Sentient starts blocking production actions."

Point out:

- destructive commands are blocked
- production deploy is blocked
- high refund requires approval
- secret leakage is blocked
- supported claims are allowed
- unsupported claims are blocked
- wrong-role refund is blocked

## Closing

"The adoption path is simple:

1. Ingest the customer's policies.
2. Route one agent workflow through Sentient.
3. Run shadow mode.
4. Generate the pilot report.
5. Review and activate rules.
6. Move selected high-risk tools to enforcement.

Sentient becomes the control plane for AI agent actions across tools, teams, and domains."

## Commands To Remember

Run full demo:

```bash
DEMO_PAUSE=1 ./scripts/live_demo.sh
```

Run without pauses:

```bash
./scripts/live_demo.sh
```

Use a different output directory:

```bash
OUTPUT_DIR=demo_output/customer_call DEMO_PAUSE=1 ./scripts/live_demo.sh
```

If you set a fixed `OUTPUT_DIR`, use a new directory for each demo so JSONL audit logs do not append old runs.

Start only the customer-facing API in shadow mode:

```bash
PYTHONPATH=src python3 -m sentient.cli serve \
  --policy examples/first_customer/customer_policy.json \
  --tool-routes examples/first_customer/tool_routes.json \
  --context-store demo_output/live_demo/context_graph/context \
  --tenant-id acme \
  --enforcement-mode shadow
```

Generate only the pilot report:

```bash
PYTHONPATH=src python3 -m sentient.cli pilot report \
  --audit demo_output/live_demo/risk_agent/audit.jsonl \
  --approvals demo_output/live_demo/risk_agent/approvals.jsonl \
  --output demo_output/live_demo/pilot-report.md
```
