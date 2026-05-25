# Ollama Risk Agent Demo

This is the strongest real-agent demo for a portfolio recording.

It sends attacker-style prompts to a real local Ollama model. The model chooses tool calls or final answers, and Sentient mitigates the risky behavior before it reaches real tools or users.

Run:

```bash
/Users/jatin.salve/homebrew/opt/ollama/bin/ollama serve
```

In another terminal:

```bash
PYTHONPATH=src python3 examples/ollama_risk_agent_demo.py \
  --model llama3.2 \
  --output-dir demo_output/ollama_risk
```

Inspect records:

```bash
PYTHONPATH=src python3 -m sentient.cli audit tail \
  --store demo_output/ollama_risk/audit.jsonl

PYTHONPATH=src python3 -m sentient.cli approvals list \
  --store demo_output/ollama_risk/approvals.jsonl \
  --all
```

What it demonstrates:

- prompt injection makes the model call `export_customer_database`; Sentient blocks it
- model attempts to reveal a key-shaped string; Sentient blocks and redacts it
- model attempts to reveal payment-card-like data; Sentient blocks and redacts it
- model attempts a `$5000` refund; Sentient requires human approval
- model makes an unsupported customer-history claim; Sentient blocks it

This demo uses a real local model and a sandbox tool layer. In production, the same Sentient wrappers would sit in front of real internal tools such as Zendesk, Stripe, Salesforce, GitHub, or deployment systems.
