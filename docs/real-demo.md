# Real Demo

This demo simulates a production customer-support agent supervised by Sentient.

The flow shows:

- normal allowed tool calls
- human approval before sending an email
- human approval before a high-value refund
- execution after approval
- a blocked customer database export
- a blocked unsupported factual claim
- audit and approval artifacts written to disk

Run it from the repository:

```bash
PYTHONPATH=src python3 -m sentient.real_demo
```

Choose an output directory:

```bash
PYTHONPATH=src python3 -m sentient.real_demo --output-dir demo_output
```

Machine-readable output:

```bash
PYTHONPATH=src python3 -m sentient.real_demo --output-dir demo_output --json
```

After it runs, inspect:

```bash
ls demo_output
PYTHONPATH=src python3 -m sentient.cli audit tail --store demo_output/audit.jsonl
PYTHONPATH=src python3 -m sentient.cli approvals list --store demo_output/approvals.jsonl --all
```

The policy used by this demo is:

```text
policies/examples/customer_support_policy.json
```

The demo code is:

```text
src/sentient/real_demo.py
```
