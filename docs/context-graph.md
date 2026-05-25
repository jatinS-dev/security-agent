# Context Graph

Sentient can ingest company policy documents and turn them into a tenant-local context graph. The graph stores source documents, chunks, extracted policy entities, draft rules, and reviewed active rules.

The v1 graph is intentionally local and inspectable. It supports Markdown, TXT, and JSON documents. Extracted rules are conservative: they stay in `draft` until a human activates them.

## LLM Brain

Sentient uses an LLM brain for cases that are too fuzzy for simple deterministic extraction.

The LLM brain has two jobs:

- extract draft rules from messy, unstructured company text
- assess complex agent events for suspicious intent, policy bypass, social engineering, or ambiguous high-risk behavior

The LLM brain does not replace deterministic policy checks. It adds understanding for complex and unstructured cases. Existing blocked-tool, role, amount, approval, and data-leak rules still remain auditable and deterministic.

The first supported provider is local Ollama. It is the default for context ingestion, context-aware policy explanation, and context-aware API serving:

```bash
/Users/jatin.salve/homebrew/opt/ollama/bin/ollama serve
/Users/jatin.salve/homebrew/opt/ollama/bin/ollama pull llama3.2
```

```bash
PYTHONPATH=src python3 -m sentient.cli context ingest \
  --tenant-id acme \
  --source examples/company_policy_pack \
  --store demo_output/context_graph/context
```

Use the same LLM brain during policy explanation:

```bash
PYTHONPATH=src python3 -m sentient.cli policy explain \
  --policy policies/default_policy.json \
  --scenario scenarios/refund_high_amount.json \
  --context-store demo_output/context_graph/context \
  --tenant-id acme
```

If the LLM is uncertain below the configured confidence threshold, its result is advisory only and does not create a violation.

For offline deterministic tests only, use `--no-llm-brain`. Context-aware production monitoring should run with the LLM brain enabled.

## Demo

```bash
PYTHONPATH=src python3 examples/context_graph_demo.py \
  --output-dir demo_output/context_graph
```

The demo ingests the sample policy pack in `examples/company_policy_pack`, activates reviewed rules, and monitors risky agent events. Decisions include source references back to the company docs.

## CLI Flow

```bash
PYTHONPATH=src python3 -m sentient.cli context ingest \
  --tenant-id acme \
  --source examples/company_policy_pack \
  --store demo_output/context_graph/context

PYTHONPATH=src python3 -m sentient.cli context rules list \
  --tenant-id acme \
  --store demo_output/context_graph/context \
  --status draft

PYTHONPATH=src python3 -m sentient.cli context rules activate \
  --tenant-id acme \
  --store demo_output/context_graph/context \
  --rule-id <rule_id> \
  --by security@example.com

PYTHONPATH=src python3 -m sentient.cli context query \
  --tenant-id acme \
  --store demo_output/context_graph/context \
  "refund approval limit"
```

## Enforcement Model

Draft rules do not affect monitoring decisions. Active rules can:

- block forbidden tools
- require approval for sensitive tools
- require approval above amount limits
- enforce role requirements
- block protected data disclosure patterns
- support factual claim verification with source chunks

Use context-aware policy testing with:

```bash
PYTHONPATH=src python3 -m sentient.cli policy test \
  --policy policies/default_policy.json \
  --all scenarios \
  --context-store demo_output/context_graph/context \
  --tenant-id acme
```

Use context-aware API decisions with:

```bash
PYTHONPATH=src python3 -m sentient.api \
  --policy policies/default_policy.json \
  --context-store demo_output/context_graph/context \
  --tenant-id acme
```
