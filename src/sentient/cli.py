from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence, TextIO

from .agent_registry import FileAgentRegistry
from .audit_integrity import verify_hash_chain
from .context import ContextAwarePolicyEngine, ContextGraph
from .controller import InMemoryAgentController
from .keys import FileApiKeyStore
from .llm import build_llm_brain
from .models import ApprovalRequest, ApprovalStatus
from .models import EnforcementMode
from .policy import Policy, PolicyEngine
from .policy_versions import (
    FilePolicyVersionStore,
    PolicyVersionRecord,
    compare_policy_files,
)
from .security import ApiSecurityConfig
from .scenarios import PolicyScenario, scenario_files
from .stores import FileApprovalStore
from .validation import load_json, validate_policy_dict
from .verifiers import KeywordEvidenceVerifier, VerifierRegistry

DEFAULT_AUDIT_PATH = Path("logs/audit.jsonl")
DEFAULT_APPROVALS_PATH = Path("logs/approvals.jsonl")
DEFAULT_CONTEXT_STORE = Path("context")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args, sys.stdout, sys.stderr)
    except (KeyError, RuntimeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sentient",
        description="Operate Sentient approvals and audit logs.",
    )
    parser.set_defaults(handler=_print_help(parser))

    subcommands = parser.add_subparsers(dest="command")
    _add_approvals_parser(subcommands)
    _add_audit_parser(subcommands)
    _add_context_parser(subcommands)
    _add_eval_parser(subcommands)
    _add_keys_parser(subcommands)
    _add_pilot_parser(subcommands)
    _add_policy_parser(subcommands)
    _add_serve_parser(subcommands)
    return parser


def _add_approvals_parser(
    subcommands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    approvals = subcommands.add_parser("approvals", help="Manage approval requests.")
    approvals.set_defaults(handler=_print_help(approvals))
    approvals_subcommands = approvals.add_subparsers(dest="approvals_command")

    list_parser = approvals_subcommands.add_parser(
        "list",
        help="List approval requests.",
    )
    list_parser.add_argument(
        "--store",
        type=Path,
        default=DEFAULT_APPROVALS_PATH,
        help="Path to approvals JSONL store.",
    )
    list_parser.add_argument(
        "--status",
        choices=[status.value for status in ApprovalStatus],
        default=ApprovalStatus.PENDING.value,
        help="Approval status to list.",
    )
    list_parser.add_argument(
        "--all",
        action="store_true",
        help="List all statuses.",
    )
    list_parser.add_argument(
        "--json",
        action="store_true",
        help="Output JSON.",
    )
    list_parser.set_defaults(handler=_approvals_list)

    approve_parser = approvals_subcommands.add_parser(
        "approve",
        help="Approve a pending request.",
    )
    _add_review_args(approve_parser)
    approve_parser.set_defaults(handler=_approvals_approve)

    reject_parser = approvals_subcommands.add_parser(
        "reject",
        help="Reject a pending request.",
    )
    _add_review_args(reject_parser)
    reject_parser.set_defaults(handler=_approvals_reject)


def _add_review_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("request_id")
    parser.add_argument(
        "--store",
        type=Path,
        default=DEFAULT_APPROVALS_PATH,
        help="Path to approvals JSONL store.",
    )
    parser.add_argument(
        "--reviewer",
        required=True,
        help="Person or system approving/rejecting the request.",
    )
    parser.add_argument(
        "--reason",
        default=None,
        help="Optional review reason.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output JSON.",
    )


def _add_audit_parser(
    subcommands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    audit = subcommands.add_parser("audit", help="Read audit records.")
    audit.set_defaults(handler=_print_help(audit))
    audit_subcommands = audit.add_subparsers(dest="audit_command")

    tail_parser = audit_subcommands.add_parser(
        "tail",
        help="Print recent audit records.",
    )
    tail_parser.add_argument(
        "--store",
        type=Path,
        default=DEFAULT_AUDIT_PATH,
        help="Path to audit JSONL store.",
    )
    tail_parser.add_argument(
        "-n",
        "--lines",
        type=int,
        default=10,
        help="Number of recent records to print.",
    )
    tail_parser.add_argument(
        "--json",
        action="store_true",
        help="Output raw JSON records.",
    )
    tail_parser.set_defaults(handler=_audit_tail)

    show_parser = audit_subcommands.add_parser(
        "show",
        help="Print all audit records.",
    )
    show_parser.add_argument(
        "--store",
        type=Path,
        default=DEFAULT_AUDIT_PATH,
        help="Path to audit JSONL store.",
    )
    show_parser.add_argument(
        "--json",
        action="store_true",
        help="Output raw JSON records.",
    )
    show_parser.set_defaults(handler=_audit_show)

    verify_parser = audit_subcommands.add_parser(
        "verify",
        help="Verify a hash-chained audit log.",
    )
    verify_parser.add_argument("--store", type=Path, required=True)
    verify_parser.set_defaults(handler=_audit_verify)

    export_parser = audit_subcommands.add_parser(
        "export",
        help="Export audit records for pilot reports.",
    )
    export_parser.add_argument(
        "--store",
        type=Path,
        default=DEFAULT_AUDIT_PATH,
        help="Path to audit JSONL store.",
    )
    export_parser.add_argument(
        "--format",
        choices=["json", "csv"],
        default="json",
        help="Export format.",
    )
    export_parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional output file. Defaults to stdout.",
    )
    export_parser.add_argument(
        "--decision-type",
        choices=["allow", "block", "require_human_approval"],
        default=None,
        help="Only export records with this decision type.",
    )
    export_parser.add_argument(
        "--enforcement-mode",
        choices=[mode.value for mode in EnforcementMode],
        default=None,
        help="Only export records from this enforcement mode.",
    )
    export_parser.add_argument(
        "--from",
        dest="from_timestamp",
        default=None,
        help="Only export records at or after this ISO timestamp.",
    )
    export_parser.add_argument(
        "--to",
        dest="to_timestamp",
        default=None,
        help="Only export records at or before this ISO timestamp.",
    )
    export_parser.set_defaults(handler=_audit_export)


def _add_context_parser(
    subcommands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    context = subcommands.add_parser("context", help="Manage company context graphs.")
    context.set_defaults(handler=_print_help(context))
    context_subcommands = context.add_subparsers(dest="context_command")

    ingest_parser = context_subcommands.add_parser(
        "ingest",
        help="Ingest company policy documents into a context graph.",
    )
    _add_context_common_args(ingest_parser)
    ingest_parser.add_argument("--source", type=Path, required=True)
    ingest_parser.add_argument("--json", action="store_true")
    _add_llm_args(ingest_parser)
    ingest_parser.set_defaults(handler=_context_ingest)

    query_parser = context_subcommands.add_parser(
        "query",
        help="Search the context graph.",
    )
    _add_context_common_args(query_parser)
    query_parser.add_argument("query")
    query_parser.add_argument("--limit", type=int, default=5)
    query_parser.add_argument("--json", action="store_true")
    query_parser.set_defaults(handler=_context_query)

    graph_parser = context_subcommands.add_parser(
        "graph",
        help="Print the stored context graph.",
    )
    _add_context_common_args(graph_parser)
    graph_parser.add_argument("--json", action="store_true")
    graph_parser.set_defaults(handler=_context_graph)

    rules_parser = context_subcommands.add_parser(
        "rules",
        help="Review context-derived policy rules.",
    )
    rules_parser.set_defaults(handler=_print_help(rules_parser))
    rules_subcommands = rules_parser.add_subparsers(dest="context_rules_command")

    list_parser = rules_subcommands.add_parser("list", help="List context rules.")
    _add_context_common_args(list_parser)
    list_parser.add_argument(
        "--status",
        choices=["draft", "active", "rejected", "all"],
        default="draft",
    )
    list_parser.add_argument("--json", action="store_true")
    list_parser.set_defaults(handler=_context_rules_list)

    activate_parser = rules_subcommands.add_parser(
        "activate",
        help="Activate a draft context rule.",
    )
    _add_context_common_args(activate_parser)
    activate_parser.add_argument("--rule-id", required=True)
    activate_parser.add_argument("--by", default=None)
    activate_parser.add_argument("--json", action="store_true")
    activate_parser.set_defaults(handler=_context_rules_activate)

    reject_parser = rules_subcommands.add_parser(
        "reject",
        help="Reject a draft context rule.",
    )
    _add_context_common_args(reject_parser)
    reject_parser.add_argument("--rule-id", required=True)
    reject_parser.add_argument("--by", default=None)
    reject_parser.add_argument("--json", action="store_true")
    reject_parser.set_defaults(handler=_context_rules_reject)


def _add_context_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument(
        "--store",
        type=Path,
        default=DEFAULT_CONTEXT_STORE,
        help="Directory containing local context graph stores.",
    )


def _add_eval_parser(
    subcommands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    eval_parser = subcommands.add_parser(
        "eval",
        help="Run customer pilot and attack scenario evaluations.",
    )
    eval_parser.set_defaults(handler=_print_help(eval_parser))
    eval_subcommands = eval_parser.add_subparsers(dest="eval_command")

    run_parser = eval_subcommands.add_parser(
        "run",
        help="Run an evaluation suite against a Sentient policy.",
    )
    run_parser.add_argument("--policy", type=Path, required=True)
    run_parser.add_argument(
        "--scenario",
        type=Path,
        action="append",
        default=[],
        help="Scenario file to run. Can be passed more than once.",
    )
    run_parser.add_argument(
        "--suite",
        type=Path,
        action="append",
        default=[],
        help="Directory or JSON file containing scenarios. Can be passed more than once.",
    )
    run_parser.add_argument(
        "--all",
        type=Path,
        default=None,
        help="Directory containing scenario JSON files.",
    )
    run_parser.add_argument("--agent-registry", type=Path, default=None)
    run_parser.add_argument("--context-store", type=Path, default=None)
    run_parser.add_argument("--tenant-id", default=None)
    run_parser.add_argument("--json", action="store_true")
    run_parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional JSON report output path.",
    )
    _add_llm_args(run_parser)
    run_parser.set_defaults(handler=_eval_run)


def _add_llm_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--llm-provider",
        choices=["ollama"],
        default="ollama",
        help="LLM brain provider used for context/risk understanding.",
    )
    parser.add_argument(
        "--no-llm-brain",
        action="store_true",
        help="Disable the LLM brain for offline deterministic checks.",
    )
    parser.add_argument("--llm-model", default="llama3.2")
    parser.add_argument("--llm-base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--llm-min-confidence", type=float, default=0.55)


def _add_keys_parser(
    subcommands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    keys = subcommands.add_parser("keys", help="Manage hashed API keys.")
    keys.set_defaults(handler=_print_help(keys))
    keys_subcommands = keys.add_subparsers(dest="keys_command")

    issue_parser = keys_subcommands.add_parser("issue", help="Issue a new API key.")
    issue_parser.add_argument("--store", type=Path, default=Path("logs/api_keys.jsonl"))
    issue_parser.add_argument("--tenant-id", required=True)
    issue_parser.add_argument("--name", default=None)
    issue_parser.add_argument("--scope", action="append", default=[])
    issue_parser.add_argument("--expires-at", default=None)
    issue_parser.add_argument("--json", action="store_true")
    issue_parser.set_defaults(handler=_keys_issue)

    list_parser = keys_subcommands.add_parser("list", help="List API key records.")
    list_parser.add_argument("--store", type=Path, default=Path("logs/api_keys.jsonl"))
    list_parser.add_argument("--tenant-id", default=None)
    list_parser.add_argument("--json", action="store_true")
    list_parser.set_defaults(handler=_keys_list)

    revoke_parser = keys_subcommands.add_parser("revoke", help="Revoke an API key.")
    revoke_parser.add_argument("key_id")
    revoke_parser.add_argument("--store", type=Path, default=Path("logs/api_keys.jsonl"))
    revoke_parser.add_argument("--json", action="store_true")
    revoke_parser.set_defaults(handler=_keys_revoke)


def _add_pilot_parser(
    subcommands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    pilot = subcommands.add_parser("pilot", help="Generate first-customer pilot artifacts.")
    pilot.set_defaults(handler=_print_help(pilot))
    pilot_subcommands = pilot.add_subparsers(dest="pilot_command")

    report_parser = pilot_subcommands.add_parser(
        "report",
        help="Generate a Markdown report from shadow-mode audit logs.",
    )
    report_parser.add_argument(
        "--audit",
        type=Path,
        default=DEFAULT_AUDIT_PATH,
        help="Path to audit JSONL store.",
    )
    report_parser.add_argument(
        "--approvals",
        type=Path,
        default=DEFAULT_APPROVALS_PATH,
        help="Path to approvals JSONL store.",
    )
    report_parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional Markdown output file. Defaults to stdout.",
    )
    report_parser.add_argument(
        "--title",
        default="Sentient Pilot Report",
        help="Report title.",
    )
    report_parser.add_argument(
        "--product-name",
        default="Sentient",
        help="Product name to use in the generated report.",
    )
    report_parser.add_argument(
        "--sample-limit",
        type=int,
        default=5,
        help="Number of sample risky events to include.",
    )
    report_parser.set_defaults(handler=_pilot_report)


def _add_policy_parser(
    subcommands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    policy = subcommands.add_parser("policy", help="Validate and test policies.")
    policy.set_defaults(handler=_print_help(policy))
    policy_subcommands = policy.add_subparsers(dest="policy_command")

    validate_parser = policy_subcommands.add_parser(
        "validate",
        help="Validate a policy JSON file.",
    )
    validate_parser.add_argument("--policy", type=Path, required=True)
    validate_parser.set_defaults(handler=_policy_validate)

    publish_parser = policy_subcommands.add_parser(
        "publish",
        help="Record a policy version.",
    )
    publish_parser.add_argument("--policy", type=Path, required=True)
    publish_parser.add_argument("--store", type=Path, default=Path("logs/policy_versions.jsonl"))
    publish_parser.add_argument("--policy-id", default=None)
    publish_parser.add_argument("--author", default=None)
    publish_parser.set_defaults(handler=_policy_publish)

    versions_parser = policy_subcommands.add_parser(
        "versions",
        help="List recorded policy versions.",
    )
    versions_parser.add_argument("--store", type=Path, default=Path("logs/policy_versions.jsonl"))
    versions_parser.add_argument("--policy-id", default=None)
    versions_parser.set_defaults(handler=_policy_versions)

    activate_parser = policy_subcommands.add_parser(
        "activate",
        help="Activate a recorded policy version.",
    )
    activate_parser.add_argument("--store", type=Path, default=Path("logs/policy_versions.jsonl"))
    activate_parser.add_argument("--policy-id", required=True)
    activate_parser.add_argument("--version", required=True)
    activate_parser.add_argument("--by", default=None)
    activate_parser.set_defaults(handler=_policy_activate)

    active_parser = policy_subcommands.add_parser(
        "active",
        help="Show the active policy version.",
    )
    active_parser.add_argument("--store", type=Path, default=Path("logs/policy_versions.jsonl"))
    active_parser.add_argument("--policy-id", required=True)
    active_parser.set_defaults(handler=_policy_active)

    rollback_parser = policy_subcommands.add_parser(
        "rollback",
        help="Roll back to the previous active policy version.",
    )
    rollback_parser.add_argument("--store", type=Path, default=Path("logs/policy_versions.jsonl"))
    rollback_parser.add_argument("--policy-id", required=True)
    rollback_parser.add_argument("--by", default=None)
    rollback_parser.set_defaults(handler=_policy_rollback)

    compare_parser = policy_subcommands.add_parser(
        "compare",
        help="Compare two policy JSON files.",
    )
    compare_parser.add_argument("--left", type=Path, required=True)
    compare_parser.add_argument("--right", type=Path, required=True)
    compare_parser.add_argument("--json", action="store_true")
    compare_parser.set_defaults(handler=_policy_compare)

    test_parser = policy_subcommands.add_parser(
        "test",
        help="Run policy scenarios.",
    )
    test_parser.add_argument("--policy", type=Path, required=True)
    test_parser.add_argument(
        "--scenario",
        type=Path,
        action="append",
        default=[],
        help="Scenario file to run. Can be passed more than once.",
    )
    test_parser.add_argument(
        "--all",
        type=Path,
        default=None,
        help="Directory containing scenario JSON files.",
    )
    test_parser.add_argument(
        "--json",
        action="store_true",
        help="Output JSON.",
    )
    test_parser.add_argument(
        "--agent-registry",
        type=Path,
        default=None,
        help="Optional agent registry JSON file.",
    )
    test_parser.add_argument(
        "--context-store",
        type=Path,
        default=None,
        help="Optional local context graph store.",
    )
    test_parser.add_argument(
        "--tenant-id",
        default=None,
        help="Tenant ID for the optional context graph store.",
    )
    _add_llm_args(test_parser)
    test_parser.set_defaults(handler=_policy_test)

    explain_parser = policy_subcommands.add_parser(
        "explain",
        help="Explain one scenario decision.",
    )
    explain_parser.add_argument("--policy", type=Path, required=True)
    explain_parser.add_argument("--scenario", type=Path, required=True)
    explain_parser.add_argument("--context-store", type=Path, default=None)
    explain_parser.add_argument("--tenant-id", default=None)
    _add_llm_args(explain_parser)
    explain_parser.set_defaults(handler=_policy_explain)


def _add_serve_parser(
    subcommands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    serve_parser = subcommands.add_parser("serve", help="Run the HTTP API server.")
    serve_parser.add_argument("--policy", type=Path, required=True)
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8080)
    serve_parser.add_argument("--audit-store", default=str(DEFAULT_AUDIT_PATH))
    serve_parser.add_argument("--approval-store", default=str(DEFAULT_APPROVALS_PATH))
    serve_parser.add_argument("--agent-registry", default=None)
    serve_parser.add_argument("--api-key", default=None)
    serve_parser.add_argument("--hmac-secret", default=None)
    serve_parser.add_argument("--rate-limit-per-minute", type=int, default=None)
    serve_parser.add_argument("--max-body-bytes", type=int, default=1_000_000)
    serve_parser.add_argument("--tenant-registry", default=None)
    serve_parser.add_argument("--api-key-store", default=None)
    serve_parser.add_argument("--context-store", default=None)
    serve_parser.add_argument("--tenant-id", default=None)
    serve_parser.add_argument("--tool-routes", default=None)
    serve_parser.add_argument(
        "--enforcement-mode",
        choices=[mode.value for mode in EnforcementMode],
        default=EnforcementMode.ENFORCE.value,
    )
    _add_llm_args(serve_parser)
    serve_parser.set_defaults(handler=_serve)


def _approvals_list(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    store = FileApprovalStore(args.store)
    status = None if args.all else ApprovalStatus(args.status)
    approvals = store.list(status)
    if args.json:
        print(json.dumps([_approval_to_dict(item) for item in approvals], indent=2), file=out)
        return 0

    if not approvals:
        print("No approval requests found.", file=out)
        return 0

    for approval in approvals:
        print(_format_approval(approval), file=out)
    return 0


def _approvals_approve(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    store = FileApprovalStore(args.store)
    approval = store.update(
        args.request_id,
        status=ApprovalStatus.APPROVED,
        reviewer=args.reviewer,
        review_reason=args.reason,
    )
    _print_review_result("approved", approval, args.json, out)
    return 0


def _approvals_reject(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    store = FileApprovalStore(args.store)
    approval = store.update(
        args.request_id,
        status=ApprovalStatus.REJECTED,
        reviewer=args.reviewer,
        review_reason=args.reason,
    )
    _print_review_result("rejected", approval, args.json, out)
    return 0


def _audit_tail(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    records = _load_jsonl(args.store)
    for record in records[-max(args.lines, 0):]:
        _print_audit_record(record, args.json, out)
    return 0


def _audit_show(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    for record in _load_jsonl(args.store):
        _print_audit_record(record, args.json, out)
    return 0


def _audit_verify(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    ok, message = verify_hash_chain(args.store)
    print(("PASS" if ok else "FAIL") + f" {args.store}: {message}", file=out if ok else err)
    return 0 if ok else 1


def _audit_export(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    records = [
        record
        for record in (_normalize_audit_record(item) for item in _load_jsonl(args.store))
        if _audit_record_matches(
            record,
            decision_type=args.decision_type,
            enforcement_mode=args.enforcement_mode,
            from_timestamp=args.from_timestamp,
            to_timestamp=args.to_timestamp,
        )
    ]
    if args.format == "csv":
        content = _audit_records_to_csv(records)
    else:
        content = json.dumps(records, indent=2, sort_keys=True)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content + "\n", encoding="utf-8")
        print(f"EXPORTED {len(records)} records -> {args.output}", file=out)
        return 0

    print(content, file=out)
    return 0


def _context_ingest(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    graph = ContextGraph(args.store, args.tenant_id)
    result = graph.ingest_path(
        args.source,
        llm_brain=_llm_brain_from_args(args),
    )
    if args.json:
        print(json.dumps(asdict(result), indent=2, sort_keys=True), file=out)
        return 0
    print(
        (
            f"INGESTED tenant={args.tenant_id} documents={result.documents_added} "
            f"chunks={result.chunks_added} draft_rules={result.rules_proposed}"
        ),
        file=out,
    )
    print(f"graph: {graph.graph_path}", file=out)
    return 0


def _context_query(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    matches = ContextGraph(args.store, args.tenant_id).query(args.query, limit=args.limit)
    if args.json:
        print(json.dumps([_context_match_to_dict(match) for match in matches], indent=2), file=out)
        return 0
    if not matches:
        print("No context matches found.", file=out)
        return 0
    for match in matches:
        document_path = match.document.path if match.document else match.chunk.path
        print(
            f"{match.score} | {document_path} | {match.chunk.heading} | {match.chunk.text}",
            file=out,
        )
    return 0


def _context_graph(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    graph = ContextGraph(args.store, args.tenant_id)
    data = graph.to_dict()
    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True), file=out)
        return 0
    print(
        (
            f"tenant={args.tenant_id} documents={len(data['documents'])} "
            f"chunks={len(data['chunks'])} nodes={len(data['nodes'])} "
            f"edges={len(data['edges'])} rules={len(data['rules'])}"
        ),
        file=out,
    )
    print(f"graph: {graph.graph_path}", file=out)
    return 0


def _context_rules_list(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    rules = ContextGraph(args.store, args.tenant_id).rules(status=args.status)
    if args.json:
        print(json.dumps([rule.to_dict() for rule in rules], indent=2, sort_keys=True), file=out)
        return 0
    if not rules:
        print("No context rules found.", file=out)
        return 0
    for rule in rules:
        print(_format_context_rule(rule), file=out)
    return 0


def _context_rules_activate(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    rule = ContextGraph(args.store, args.tenant_id).activate_rule(
        args.rule_id,
        reviewed_by=args.by,
    )
    _print_context_rule_update("ACTIVE", rule, args.json, out)
    return 0


def _context_rules_reject(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    rule = ContextGraph(args.store, args.tenant_id).reject_rule(
        args.rule_id,
        reviewed_by=args.by,
    )
    _print_context_rule_update("REJECTED", rule, args.json, out)
    return 0


def _keys_issue(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    raw_key, record = FileApiKeyStore(args.store).issue(
        tenant_id=args.tenant_id,
        scopes=tuple(args.scope or ["*"]),
        name=args.name,
        expires_at=args.expires_at,
    )
    payload = {**_api_key_to_dict(record), "api_key": raw_key}
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True), file=out)
    else:
        print(f"ISSUED {record.key_id} tenant={record.tenant_id}", file=out)
        print(f"api_key: {raw_key}", file=out)
    return 0


def _keys_list(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    records = FileApiKeyStore(args.store).list(args.tenant_id)
    if args.json:
        print(json.dumps([_api_key_to_dict(record) for record in records], indent=2), file=out)
        return 0
    if not records:
        print("No API keys found.", file=out)
        return 0
    for record in records:
        revoked = "revoked" if record.revoked_at else "active"
        scopes = ",".join(record.scopes)
        print(f"{record.key_id} | {record.tenant_id} | {scopes} | {revoked} | {record.name or ''}", file=out)
    return 0


def _keys_revoke(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    record = FileApiKeyStore(args.store).revoke(args.key_id)
    if args.json:
        print(json.dumps(_api_key_to_dict(record), indent=2), file=out)
    else:
        print(f"REVOKED {record.key_id}", file=out)
    return 0


def _pilot_report(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    records = [_normalize_audit_record(item) for item in _load_jsonl(args.audit)]
    approvals = FileApprovalStore(args.approvals).list(None)
    content = _build_pilot_report_markdown(
        title=args.title,
        audit_path=args.audit,
        approvals_path=args.approvals,
        records=records,
        approvals=approvals,
        sample_limit=max(args.sample_limit, 0),
        product_name=args.product_name,
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content + "\n", encoding="utf-8")
        print(f"REPORT {args.output}", file=out)
        return 0
    print(content, file=out)
    return 0


def _policy_validate(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    errors = validate_policy_dict(load_json(args.policy))
    if errors:
        for error in errors:
            print(f"ERROR {args.policy}: {error}", file=err)
        return 1
    print(f"PASS {args.policy}", file=out)
    return 0


def _policy_publish(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    data = load_json(args.policy)
    errors = validate_policy_dict(data)
    if errors:
        for error in errors:
            print(f"ERROR {args.policy}: {error}", file=err)
        return 1
    policy_id = args.policy_id or data["name"].lower().replace(" ", "-")
    record = PolicyVersionRecord(
        policy_id=policy_id,
        version=data["version"],
        path=str(args.policy),
        created_at=datetime.now(timezone.utc).isoformat(),
        author=args.author,
    )
    FilePolicyVersionStore(args.store).publish(record)
    print(f"PUBLISHED {record.policy_id}@{record.version}", file=out)
    return 0


def _policy_versions(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    records = FilePolicyVersionStore(args.store).list(args.policy_id)
    if not records:
        print("No policy versions found.", file=out)
        return 0
    for record in records:
        print(
            f"{record.policy_id} | {record.version} | {record.path} | {record.created_at} | {record.author or ''}",
            file=out,
        )
    return 0


def _policy_activate(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    active = FilePolicyVersionStore(args.store).activate(
        args.policy_id,
        args.version,
        activated_by=args.by,
    )
    print(f"ACTIVE {active.policy_id}@{active.version} -> {active.path}", file=out)
    return 0


def _policy_active(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    active = FilePolicyVersionStore(args.store).active(args.policy_id)
    if active is None:
        print(f"No active policy for {args.policy_id}.", file=out)
        return 0
    print(
        f"{active.policy_id} | {active.version} | {active.path} | {active.activated_at} | {active.activated_by or ''}",
        file=out,
    )
    return 0


def _policy_rollback(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    active = FilePolicyVersionStore(args.store).rollback(args.policy_id, activated_by=args.by)
    print(f"ROLLED_BACK {active.policy_id}@{active.version} -> {active.path}", file=out)
    return 0


def _policy_compare(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    diff = compare_policy_files(args.left, args.right)
    if args.json:
        print(json.dumps(diff, indent=2, sort_keys=True), file=out)
        return 0
    print(f"left: {diff['left']}", file=out)
    print(f"right: {diff['right']}", file=out)
    print(f"added: {', '.join(diff['added']) or '-'}", file=out)
    print(f"removed: {', '.join(diff['removed']) or '-'}", file=out)
    changed = ", ".join(diff["changed"].keys())
    print(f"changed: {changed or '-'}", file=out)
    return 0


def _policy_test(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    policy_errors = validate_policy_dict(load_json(args.policy))
    if policy_errors:
        for error in policy_errors:
            print(f"ERROR {args.policy}: {error}", file=err)
        return 1

    paths = _collect_scenario_paths(args)
    if not paths:
        print("No scenarios provided.", file=err)
        return 1

    policy = Policy.from_file(args.policy)
    agent_registry = (
        FileAgentRegistry.from_file(args.agent_registry)
        if args.agent_registry
        else None
    )
    results = []
    for path in paths:
        scenario = PolicyScenario.from_file(path)
        controller = InMemoryAgentController()
        controller.register(scenario.event.agent_id)
        engine = _build_policy_engine(
            policy,
            agent_registry=agent_registry,
            context_store=args.context_store,
            tenant_id=args.tenant_id,
            llm_brain=_llm_brain_from_args(args),
        )
        decision = engine.evaluate(scenario.event)
        decision_type = _decision_type_from_violations(decision)
        passed = decision_type == scenario.expected_decision
        result = {
            "path": str(path),
            "name": scenario.name,
            "passed": passed,
            "expected": scenario.expected_decision.value,
            "actual": decision_type.value,
        }
        results.append(result)
        if not args.json:
            status = "PASS" if passed else "FAIL"
            print(
                f"{status} {scenario.name}: expected {scenario.expected_decision.value}, got {decision_type.value}",
                file=out,
            )

    if args.json:
        print(json.dumps(results, indent=2), file=out)
    return 0 if all(result["passed"] for result in results) else 1


def _eval_run(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    policy_errors = validate_policy_dict(load_json(args.policy))
    if policy_errors:
        for error in policy_errors:
            print(f"ERROR {args.policy}: {error}", file=err)
        return 1

    paths = _collect_scenario_paths(args)
    if not paths:
        print("No scenarios provided.", file=err)
        return 1

    policy = Policy.from_file(args.policy)
    agent_registry = (
        FileAgentRegistry.from_file(args.agent_registry)
        if args.agent_registry
        else None
    )
    llm_brain = _llm_brain_from_args(args)
    results = []
    for path in paths:
        scenario = PolicyScenario.from_file(path)
        engine = _build_policy_engine(
            policy,
            agent_registry=agent_registry,
            context_store=args.context_store,
            tenant_id=args.tenant_id,
            llm_brain=llm_brain,
        )
        violations = engine.evaluate(scenario.event)
        actual = _decision_type_from_violations(violations)
        results.append(
            {
                "path": str(path),
                "name": scenario.name,
                "agent_id": scenario.event.agent_id,
                "event_type": scenario.event.event_type.value,
                "expected": scenario.expected_decision.value,
                "actual": actual.value,
                "passed": actual == scenario.expected_decision,
                "violations": [_policy_violation_to_dict(item) for item in violations],
            }
        )

    report = _eval_report(args.policy, results)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True), file=out)
    else:
        print("Sentient Eval", file=out)
        print("=============", file=out)
        print(
            f"policy: {args.policy}",
            file=out,
        )
        print(
            f"total={report['total']} passed={report['passed']} failed={report['failed']}",
            file=out,
        )
        if args.output:
            print(f"report: {args.output}", file=out)
        for result in results:
            status = "PASS" if result["passed"] else "FAIL"
            print(
                (
                    f"{status} {result['name']}: "
                    f"expected {result['expected']}, got {result['actual']}"
                ),
                file=out,
            )
            if not result["passed"] and result["violations"]:
                for violation in result["violations"]:
                    print(
                        f"  - {violation['rule_id']}: {violation['description']}",
                        file=out,
                    )
    return 0 if report["failed"] == 0 else 1


def _policy_explain(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    policy = Policy.from_file(args.policy)
    scenario = PolicyScenario.from_file(args.scenario)
    engine = _build_policy_engine(
        policy,
        context_store=args.context_store,
        tenant_id=args.tenant_id,
        llm_brain=_llm_brain_from_args(args),
    )
    violations = engine.evaluate(scenario.event)
    decision_type = _decision_type_from_violations(violations)
    print(f"scenario: {scenario.name}", file=out)
    print(f"decision: {decision_type.value}", file=out)
    if not violations:
        print("reason: no policy violations", file=out)
    for violation in violations:
        print(
            f"- {violation.rule_id}: {violation.description} ({violation.evidence})",
            file=out,
        )
    return 0


def _serve(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
    from .api import serve

    api_key_store = FileApiKeyStore(args.api_key_store) if args.api_key_store else None
    serve(
        args.policy,
        host=args.host,
        port=args.port,
        audit_path=args.audit_store,
        approvals_path=args.approval_store,
        registry_path=args.agent_registry,
        tenant_registry_path=args.tenant_registry,
        context_store=args.context_store,
        tenant_id=args.tenant_id,
        llm_provider=args.llm_provider,
        llm_model=args.llm_model,
        llm_base_url=args.llm_base_url,
        llm_min_confidence=args.llm_min_confidence,
        tool_routes_path=args.tool_routes,
        enforcement_mode=args.enforcement_mode,
        security=ApiSecurityConfig(
            api_key=args.api_key,
            hmac_secret=args.hmac_secret,
            rate_limit_per_minute=args.rate_limit_per_minute,
            max_body_bytes=args.max_body_bytes,
            api_key_store=api_key_store,
        ),
    )
    return 0


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            records.append(json.loads(line))
    return records


def _normalize_audit_record(record: dict[str, Any]) -> dict[str, Any]:
    return record.get("record", record)


def _audit_record_matches(
    record: dict[str, Any],
    *,
    decision_type: str | None,
    enforcement_mode: str | None,
    from_timestamp: str | None,
    to_timestamp: str | None,
) -> bool:
    decision = record.get("decision", {})
    timestamp = str(record.get("timestamp", ""))
    if decision_type is not None and decision.get("decision_type") != decision_type:
        return False
    if enforcement_mode is not None and decision.get("enforcement_mode") != enforcement_mode:
        return False
    if from_timestamp is not None and timestamp < from_timestamp:
        return False
    if to_timestamp is not None and timestamp > to_timestamp:
        return False
    return True


def _audit_records_to_csv(records: list[dict[str, Any]]) -> str:
    output = io.StringIO()
    fieldnames = [
        "timestamp",
        "agent_id",
        "task_id",
        "event_type",
        "decision_type",
        "enforcement_mode",
        "enforced",
        "summary",
        "violation_count",
        "rule_ids",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for record in records:
        event = record.get("event", {})
        decision = record.get("decision", {})
        violations = decision.get("violations", [])
        writer.writerow(
            {
                "timestamp": record.get("timestamp", ""),
                "agent_id": event.get("agent_id", ""),
                "task_id": event.get("task_id", ""),
                "event_type": event.get("event_type", ""),
                "decision_type": decision.get("decision_type", ""),
                "enforcement_mode": decision.get("enforcement_mode", ""),
                "enforced": decision.get("enforced", ""),
                "summary": decision.get("summary", ""),
                "violation_count": len(violations),
                "rule_ids": ",".join(
                    str(violation.get("rule_id", ""))
                    for violation in violations
                ),
            }
        )
    return output.getvalue().rstrip("\r\n")


def _collect_scenario_paths(args: argparse.Namespace) -> list[Path]:
    paths = list(getattr(args, "scenario", []))
    for suite in getattr(args, "suite", []):
        paths.extend(scenario_files(suite))
    all_path = getattr(args, "all", None)
    if all_path is not None:
        paths.extend(scenario_files(all_path))
    return paths


def _policy_violation_to_dict(violation: Any) -> dict[str, Any]:
    return {
        "rule_id": violation.rule_id,
        "description": violation.description,
        "severity": violation.severity.value,
        "action": violation.action,
        "evidence": violation.evidence,
    }


def _eval_report(policy_path: Path, results: list[dict[str, Any]]) -> dict[str, Any]:
    passed = sum(1 for result in results if result["passed"])
    failed = len(results) - passed
    return {
        "policy": str(policy_path),
        "total": len(results),
        "passed": passed,
        "failed": failed,
        "results": results,
    }


def _build_pilot_report_markdown(
    *,
    title: str,
    audit_path: Path,
    approvals_path: Path,
    records: list[dict[str, Any]],
    approvals: list[ApprovalRequest],
    sample_limit: int,
    product_name: str = "Sentient",
) -> str:
    generated_at = datetime.now(timezone.utc).isoformat()
    decisions = [record.get("decision", {}) for record in records]
    events = [record.get("event", {}) for record in records]
    risky_records = [
        record
        for record in records
        if record.get("decision", {}).get("decision_type") in {"block", "require_human_approval"}
    ]
    shadow_records = [
        record
        for record in risky_records
        if record.get("decision", {}).get("enforcement_mode") == EnforcementMode.SHADOW.value
        or record.get("decision", {}).get("enforced") is False
    ]
    would_block = [
        record
        for record in shadow_records
        if record.get("decision", {}).get("decision_type") == "block"
    ]
    would_approval = [
        record
        for record in shadow_records
        if record.get("decision", {}).get("decision_type") == "require_human_approval"
    ]
    enforced_block = [
        record
        for record in risky_records
        if record.get("decision", {}).get("decision_type") == "block"
        and record.get("decision", {}).get("enforced") is not False
    ]
    enforced_approval = [
        record
        for record in risky_records
        if record.get("decision", {}).get("decision_type") == "require_human_approval"
        and record.get("decision", {}).get("enforced") is not False
    ]

    decision_counts = Counter(str(decision.get("decision_type", "unknown")) for decision in decisions)
    rule_counts: Counter[str] = Counter()
    rule_descriptions: dict[str, str] = {}
    risky_tool_counts: Counter[str] = Counter()
    agent_violation_counts: Counter[str] = Counter()
    for record in risky_records:
        event = record.get("event", {})
        decision = record.get("decision", {})
        tool_name = _audit_tool_name(event)
        if tool_name:
            risky_tool_counts[tool_name] += 1
        agent_id = str(event.get("agent_id", "unknown"))
        agent_violation_counts[agent_id] += 1
        for violation in decision.get("violations", []):
            rule_id = str(violation.get("rule_id", "unknown-rule"))
            rule_counts[rule_id] += 1
            rule_descriptions.setdefault(rule_id, str(violation.get("description", "")))

    approval_counts = Counter(approval.status.value for approval in approvals)
    lines = [
        f"# {title}",
        "",
        f"Generated: `{generated_at}`",
        "",
        "## Inputs",
        "",
        f"- Audit log: `{audit_path}`",
        f"- Approvals log: `{approvals_path}`",
        "",
        "## Executive Summary",
        "",
        f"- Total monitored actions: **{len(records)}**",
        f"- Allowed actions: **{decision_counts.get('allow', 0)}**",
        f"- Risky decisions: **{len(risky_records)}**",
        f"- Would-have blocked in shadow mode: **{len(would_block)}**",
        f"- Would-have required approval in shadow mode: **{len(would_approval)}**",
        f"- Enforced blocked actions: **{len(enforced_block)}**",
        f"- Enforced approval-required actions: **{len(enforced_approval)}**",
        f"- Approval requests: **{len(approvals)}**",
        "",
        "## Decision Breakdown",
        "",
        _markdown_table(
            ["Decision", "Count"],
            [
                [decision, str(count)]
                for decision, count in sorted(decision_counts.items())
            ],
        ),
        "",
        "## Approval Queue",
        "",
        _markdown_table(
            ["Status", "Count"],
            [
                [status, str(count)]
                for status, count in sorted(approval_counts.items())
            ],
        )
        if approvals
        else "No approval requests were recorded.",
        "",
        "## Top Violated Rules",
        "",
        _top_rules_markdown(rule_counts, rule_descriptions),
        "",
        "## Risky Tools",
        "",
        _counter_table_markdown("Tool", risky_tool_counts),
        "",
        "## Agents With Most Violations",
        "",
        _counter_table_markdown("Agent", agent_violation_counts),
        "",
        "## Sample Risky Events",
        "",
        _sample_events_markdown(risky_records, sample_limit),
        "",
        "## Recommended First Enforcement Rules",
        "",
        _recommendations_markdown(shadow_records, rule_counts, rule_descriptions),
        "",
        "## Shadow-To-Enforce Readiness Checklist",
        "",
        f"- [ ] All selected production tools route through {product_name}.",
        "- [ ] Shadow findings have been reviewed with the customer owner.",
        "- [ ] Noisy draft/context rules have been rejected or refined.",
        "- [ ] Approval owners are assigned for approval-required tools.",
        "- [ ] Scenario evals pass for the customer policy pack.",
        "- [ ] Customer has approved the first tools/rules to enforce.",
        "- [ ] Fail-open/fail-closed behavior is documented per critical route.",
    ]
    return "\n".join(lines)


def _audit_tool_name(event: dict[str, Any]) -> str:
    metadata = event.get("metadata", {})
    if isinstance(metadata, dict) and metadata.get("tool_name"):
        return str(metadata["tool_name"])
    if event.get("tool_name"):
        return str(event["tool_name"])
    return ""


def _markdown_table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return "No records."
    header = "| " + " | ".join(headers) + " |"
    separator = "| " + " | ".join("---" for _ in headers) + " |"
    body = [
        "| " + " | ".join(_markdown_cell(value) for value in row) + " |"
        for row in rows
    ]
    return "\n".join([header, separator, *body])


def _markdown_cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _counter_table_markdown(label: str, counts: Counter[str], *, limit: int = 10) -> str:
    if not counts:
        return "No risky records."
    return _markdown_table(
        [label, "Count"],
        [[name, str(count)] for name, count in counts.most_common(limit)],
    )


def _top_rules_markdown(
    rule_counts: Counter[str],
    rule_descriptions: dict[str, str],
    *,
    limit: int = 10,
) -> str:
    if not rule_counts:
        return "No policy violations were recorded."
    return _markdown_table(
        ["Rule", "Count", "Description"],
        [
            [rule_id, str(count), rule_descriptions.get(rule_id, "")]
            for rule_id, count in rule_counts.most_common(limit)
        ],
    )


def _sample_events_markdown(records: list[dict[str, Any]], sample_limit: int) -> str:
    if not records or sample_limit == 0:
        return "No risky sample events."
    rows = []
    for record in records[:sample_limit]:
        event = record.get("event", {})
        decision = record.get("decision", {})
        rows.append(
            [
                str(record.get("timestamp", "")),
                str(event.get("agent_id", "")),
                _audit_tool_name(event) or str(event.get("event_type", "")),
                str(decision.get("decision_type", "")),
                str(decision.get("enforcement_mode", "")),
                str(decision.get("summary", "")),
            ]
        )
    return _markdown_table(
        ["Timestamp", "Agent", "Tool/Event", "Decision", "Mode", "Summary"],
        rows,
    )


def _recommendations_markdown(
    shadow_records: list[dict[str, Any]],
    rule_counts: Counter[str],
    rule_descriptions: dict[str, str],
) -> str:
    if not shadow_records:
        return "No shadow-mode risky decisions were recorded. Keep collecting representative traffic before moving new rules to enforce mode."
    lines = []
    for rule_id, count in rule_counts.most_common(5):
        description = rule_descriptions.get(rule_id, "Review this rule with the customer owner.")
        lines.append(f"- Enforce `{rule_id}` after review: {description} Seen {count} time(s).")
    lines.append("- Keep shadow mode enabled for low-confidence or noisy rules until the customer approves the behavior.")
    return "\n".join(lines)


def _decision_type_from_violations(violations: tuple[Any, ...]):
    from .models import DecisionType

    if any(
        violation.action == "stop_agent"
        or violation.severity.value in {"high", "critical"}
        for violation in violations
    ):
        return DecisionType.BLOCK
    if any(violation.action == "require_human_approval" for violation in violations):
        return DecisionType.REQUIRE_HUMAN_APPROVAL
    return DecisionType.ALLOW


def _build_policy_engine(
    policy: Policy,
    *,
    agent_registry: Any | None = None,
    context_store: Path | None = None,
    tenant_id: str | None = None,
    llm_brain: Any | None = None,
) -> PolicyEngine:
    verifier_registry = VerifierRegistry(KeywordEvidenceVerifier())
    if context_store is None and llm_brain is None:
        return PolicyEngine(
            policy,
            verifier_registry=verifier_registry,
            agent_registry=agent_registry,
        )
    if context_store is not None and llm_brain is None:
        raise ValueError(
            "Context-aware monitoring requires the LLM brain. "
            "Use --llm-provider ollama, or pass --no-llm-brain only for offline deterministic tests."
        )
    if context_store is not None and not tenant_id:
        raise ValueError("--tenant-id is required when --context-store is provided")
    return ContextAwarePolicyEngine(
        policy,
        context_graph=ContextGraph(context_store, tenant_id) if context_store else None,
        llm_brain=llm_brain,
        verifier_registry=verifier_registry,
        agent_registry=agent_registry,
    )


def _llm_brain_from_args(args: argparse.Namespace):
    if getattr(args, "no_llm_brain", False):
        return None
    return build_llm_brain(
        getattr(args, "llm_provider", "ollama"),
        model=getattr(args, "llm_model", "llama3.2"),
        base_url=getattr(args, "llm_base_url", "http://127.0.0.1:11434"),
        min_confidence=getattr(args, "llm_min_confidence", 0.55),
    )


def _context_match_to_dict(match) -> dict[str, Any]:
    return {
        "score": match.score,
        "document": asdict(match.document) if match.document else None,
        "chunk": asdict(match.chunk),
    }


def _format_context_rule(rule) -> str:
    details = []
    if rule.tool_name:
        details.append(f"tool={rule.tool_name}")
    if rule.max_amount is not None:
        details.append(f"max={rule.max_amount:g}")
    if rule.allowed_roles:
        details.append(f"roles={','.join(rule.allowed_roles)}")
    if rule.pattern:
        details.append(f"pattern={rule.pattern}")
    detail_text = " | " + " | ".join(details) if details else ""
    return (
        f"{rule.id} | {rule.status} | {rule.rule_type} | "
        f"{rule.action} | {rule.description}{detail_text}"
    )


def _print_context_rule_update(
    status: str,
    rule,
    as_json: bool,
    out: TextIO,
) -> None:
    if as_json:
        print(json.dumps(rule.to_dict(), indent=2, sort_keys=True), file=out)
        return
    print(f"{status} {rule.id} | {rule.rule_type} | {rule.description}", file=out)


def _print_review_result(
    action: str,
    approval: ApprovalRequest,
    as_json: bool,
    out: TextIO,
) -> None:
    if as_json:
        print(json.dumps(_approval_to_dict(approval), indent=2), file=out)
        return
    print(f"{action}: {approval.request_id} ({approval.tool_name})", file=out)


def _print_audit_record(record: dict[str, Any], as_json: bool, out: TextIO) -> None:
    if as_json:
        print(json.dumps(record, sort_keys=True), file=out)
        return

    event = record.get("event", {})
    decision = record.get("decision", {})
    timestamp = record.get("timestamp", "")
    print(
        " | ".join(
            [
                timestamp,
                str(event.get("agent_id", "")),
                str(event.get("event_type", "")),
                str(decision.get("decision_type", "")),
                str(decision.get("summary", "")),
            ]
        ),
        file=out,
    )


def _format_approval(approval: ApprovalRequest) -> str:
    return " | ".join(
        [
            approval.request_id,
            approval.status.value,
            approval.agent_id,
            approval.tool_name,
            approval.task_id or "",
            approval.decision_summary,
        ]
    )


def _approval_to_dict(approval: ApprovalRequest) -> dict[str, Any]:
    return {
        "request_id": approval.request_id,
        "agent_id": approval.agent_id,
        "task_id": approval.task_id,
        "tool_name": approval.tool_name,
        "tool_args": approval.tool_args,
        "metadata": approval.metadata,
        "decision_summary": approval.decision_summary,
        "status": approval.status.value,
        "created_at": approval.created_at,
        "reviewed_at": approval.reviewed_at,
        "reviewer": approval.reviewer,
        "review_reason": approval.review_reason,
    }


def _api_key_to_dict(record) -> dict[str, Any]:
    return {
        "key_id": record.key_id,
        "tenant_id": record.tenant_id,
        "scopes": list(record.scopes),
        "created_at": record.created_at,
        "name": record.name,
        "expires_at": record.expires_at,
        "revoked_at": record.revoked_at,
    }


def _print_help(parser: argparse.ArgumentParser):
    def handler(args: argparse.Namespace, out: TextIO, err: TextIO) -> int:
        parser.print_help(out)
        return 0

    return handler


if __name__ == "__main__":
    raise SystemExit(main())
