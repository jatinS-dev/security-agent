# Ollama Local Demo

This demo uses a free local model through Ollama. No OpenAI key, paid API, or quota is required.

Sentient still supervises every tool call before execution:

- allowed ticket and knowledge-base reads
- approval gates for email and high-value refunds
- blocked customer database export
- audit and approval artifacts

## Install Ollama

Install Ollama from:

```text
https://ollama.com
```

Pull a local model:

```bash
ollama pull llama3.2
```

You can use a different local model if you already have one.

## Run

```bash
PYTHONPATH=src python3 examples/ollama_local_support_demo.py \
  --model llama3.2 \
  --output-dir demo_output/ollama
```

Machine-readable output:

```bash
PYTHONPATH=src python3 examples/ollama_local_support_demo.py \
  --model llama3.2 \
  --output-dir demo_output/ollama \
  --json
```

## Inspect Sentient Artifacts

```bash
PYTHONPATH=src python3 -m sentient.cli audit tail \
  --store demo_output/ollama/audit.jsonl

PYTHONPATH=src python3 -m sentient.cli approvals list \
  --store demo_output/ollama/approvals.jsonl \
  --all
```

## What Is Real

The model loop is real: a local Ollama model chooses tool calls by returning JSON actions. Sentient enforces policy before each tool executes.

The business systems are sandboxed:

- ticket lookup
- knowledge-base lookup
- email artifact writer
- refund artifact writer

For production, replace the sandbox methods with real internal APIs while keeping Sentient in front of each tool.
