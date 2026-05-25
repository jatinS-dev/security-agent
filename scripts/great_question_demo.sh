#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

RUN_ID="$(date +%Y%m%d_%H%M%S)"
OUTPUT_DIR="${OUTPUT_DIR:-demo_output/great_question/$RUN_ID}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
DEMO_PAUSE="${DEMO_PAUSE:-0}"

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

mkdir -p "$OUTPUT_DIR"

section "Great Question Application Demo"
printf "Output directory: %s\n" "$OUTPUT_DIR"
printf "Read-aloud script: docs/great-question-demo-script.md\n"
pause_if_needed

section "1. AI Research Assistant Safety Demo"
run_cmd "$PYTHON_BIN" examples/great_question_ai_research_demo.py --output-dir "$OUTPUT_DIR"
pause_if_needed

section "2. Audit Trail"
run_cmd "$PYTHON_BIN" scripts/research_guard_cli.py audit tail --store "$OUTPUT_DIR/audit.jsonl" --lines 20
pause_if_needed

section "3. Approval Queue"
run_cmd "$PYTHON_BIN" scripts/research_guard_cli.py approvals list --store "$OUTPUT_DIR/approvals.jsonl" --all
pause_if_needed

section "4. Pilot Report"
run_cmd "$PYTHON_BIN" scripts/research_guard_cli.py pilot report \
  --audit "$OUTPUT_DIR/audit.jsonl" \
  --approvals "$OUTPUT_DIR/approvals.jsonl" \
  --title "Great Question AI Research Safety Demo Report" \
  --product-name "Research Guard" \
  --output "$OUTPUT_DIR/pilot-report.md"
run_cmd sed -n "1,220p" "$OUTPUT_DIR/pilot-report.md"
pause_if_needed

section "Done"
printf "%s\n" "- Demo summary: $OUTPUT_DIR/great-question-demo-summary.json"
printf "%s\n" "- Audit log: $OUTPUT_DIR/audit.jsonl"
printf "%s\n" "- Approvals: $OUTPUT_DIR/approvals.jsonl"
printf "%s\n" "- Pilot report: $OUTPUT_DIR/pilot-report.md"
