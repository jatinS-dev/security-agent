#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

RUN_ID="$(date +%Y%m%d_%H%M%S)"
OUTPUT_DIR="${OUTPUT_DIR:-demo_output/live_demo/$RUN_ID}"
MODEL="${MODEL:-llama3.2}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
OLLAMA_URL="${OLLAMA_URL:-http://127.0.0.1:11434}"
DEMO_PAUSE="${DEMO_PAUSE:-0}"
REQUIRE_OLLAMA="${REQUIRE_OLLAMA:-0}"

export PYTHONPATH="${PYTHONPATH:-src}"

section() {
  printf "\n"
  printf "============================================================\n"
  printf "%s\n" "$1"
  printf "============================================================\n"
}

run_cmd() {
  printf "\n$ %s\n" "$*"
  "$@"
}

pause_if_needed() {
  if [[ "$DEMO_PAUSE" == "1" ]]; then
    printf "\nPress Enter to continue..."
    read -r _
  fi
}

ollama_is_running() {
  curl -fsS "$OLLAMA_URL/api/tags" >/dev/null 2>&1
}

mkdir -p "$OUTPUT_DIR"

section "Sentient Live Demo"
printf "Output directory: %s\n" "$OUTPUT_DIR"
printf "Model: %s\n" "$MODEL"
printf "Presenter script: docs/live-demo-script.md\n"
pause_if_needed

section "1. Customer Policy Context"
printf "Sentient ingests company policy docs, builds a context graph, proposes rules, and monitors agent behavior with citations.\n"
run_cmd "$PYTHON_BIN" examples/context_graph_demo.py --output-dir "$OUTPUT_DIR/context_graph"
pause_if_needed

section "2. Real Risky Agent Behavior"
if ollama_is_running; then
  printf "Ollama is running. Using a real local LLM agent for this demo.\n"
  run_cmd "$PYTHON_BIN" examples/ollama_risk_agent_demo.py --model "$MODEL" --output-dir "$OUTPUT_DIR/risk_agent"
else
  printf "Ollama is not reachable at %s.\n" "$OLLAMA_URL"
  printf "To run the real LLM path, start Ollama first:\n"
  printf "  /Users/jatin.salve/homebrew/opt/ollama/bin/ollama serve\n"
  if [[ "$REQUIRE_OLLAMA" == "1" ]]; then
    printf "REQUIRE_OLLAMA=1 is set, so stopping here.\n"
    exit 1
  fi
  printf "Falling back to deterministic risk showcase so the demo can continue.\n"
  run_cmd "$PYTHON_BIN" -m sentient.risk_showcase --output-dir "$OUTPUT_DIR/risk_agent"
fi
pause_if_needed

section "3. Audit Trail"
printf "The audit log shows what Sentient allowed, blocked, or escalated.\n"
run_cmd "$PYTHON_BIN" -m sentient.cli audit tail --store "$OUTPUT_DIR/risk_agent/audit.jsonl" --lines 20
pause_if_needed

section "4. Shadow Mode Tool Proxy"
printf "For customer pilots, Sentient can forward risky calls but still record the would-have decision.\n"
run_cmd "$PYTHON_BIN" examples/tool_proxy_demo.py
pause_if_needed

section "5. Approval Queue"
printf "High-risk actions can require human approval instead of being executed automatically.\n"
run_cmd "$PYTHON_BIN" -m sentient.cli approvals list --store "$OUTPUT_DIR/risk_agent/approvals.jsonl" --all
pause_if_needed

section "6. Customer Pilot Report"
printf "Sentient turns shadow/enforcement findings into a customer-readable Markdown report.\n"
run_cmd "$PYTHON_BIN" -m sentient.cli pilot report \
  --audit "$OUTPUT_DIR/risk_agent/audit.jsonl" \
  --approvals "$OUTPUT_DIR/risk_agent/approvals.jsonl" \
  --output "$OUTPUT_DIR/pilot-report.md"
run_cmd sed -n "1,220p" "$OUTPUT_DIR/pilot-report.md"
pause_if_needed

section "7. Scenario Eval Before Enforcement"
printf "Before moving a customer from shadow mode to enforcement, replay their policy scenarios.\n"
run_cmd "$PYTHON_BIN" -m sentient.cli eval run \
  --policy policies/default_policy.json \
  --suite scenarios \
  --no-llm-brain \
  --output "$OUTPUT_DIR/eval-report.json"
pause_if_needed

section "Demo Complete"
printf "Key artifacts:\n"
printf "%s\n" "- Context graph: $OUTPUT_DIR/context_graph/context/acme/context_graph.json"
printf "%s\n" "- Audit log: $OUTPUT_DIR/risk_agent/audit.jsonl"
printf "%s\n" "- Approvals: $OUTPUT_DIR/risk_agent/approvals.jsonl"
printf "%s\n" "- Pilot report: $OUTPUT_DIR/pilot-report.md"
printf "%s\n" "- Eval report: $OUTPUT_DIR/eval-report.json"
