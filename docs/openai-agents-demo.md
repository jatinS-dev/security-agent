# OpenAI Agents SDK Demo

This demo runs a real OpenAI Agents SDK agent with Sentient-guarded function tools.

It is different from `sentient.real_demo`:

- `sentient.real_demo` is deterministic and does not call a model.
- `examples/openai_agents_real_support_demo.py` uses `Agent`, `Runner.run_sync`, and `function_tool` from the OpenAI Agents SDK.
- Sentient still sits between the agent and every local tool execution.

## Install

```bash
python3 -m pip install ".[openai]"
```

Set your OpenAI API key:

```bash
export OPENAI_API_KEY="..."
```

## Run

```bash
PYTHONPATH=src python3 examples/openai_agents_real_support_demo.py \
  --output-dir demo_output/openai_agents
```

Use a different model:

```bash
PYTHONPATH=src python3 examples/openai_agents_real_support_demo.py \
  --model gpt-5-nano \
  --output-dir demo_output/openai_agents
```

Machine-readable output:

```bash
PYTHONPATH=src python3 examples/openai_agents_real_support_demo.py \
  --output-dir demo_output/openai_agents \
  --json
```

## Inspect Sentient Artifacts

```bash
PYTHONPATH=src python3 -m sentient.cli audit tail \
  --store demo_output/openai_agents/audit.jsonl

PYTHONPATH=src python3 -m sentient.cli approvals list \
  --store demo_output/openai_agents/approvals.jsonl \
  --all
```

## What Is Real

The agent loop is real: the OpenAI Agents SDK decides which tools to call. Sentient enforces policy inside those tool calls before the sandbox tools execute.

The business systems are still sandboxed for safety:

- ticketing sandbox
- knowledge-base sandbox
- email artifact writer
- refund artifact writer

This is the right shape for production adoption: replace the sandbox methods in `SupportSandbox` with Zendesk, Stripe test mode, Salesforce, Slack, or internal APIs, while keeping the Sentient wrapper in front of each tool.
