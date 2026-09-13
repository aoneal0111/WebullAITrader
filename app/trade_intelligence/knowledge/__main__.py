"""Offline CLI: ``python -m app.trade_intelligence.knowledge``."""

from __future__ import annotations

import argparse
from datetime import date
import json
import os
from pathlib import Path
import tempfile

from .acquisition import AcquisitionConfig, AlpacaHistoricalClient
from .candidate_days import discover_candidate_days
from .accounting import status_for_root
from .mining import JsonlBarProvider, build_corpus
from .models import ACTIVE_STRATEGIES
from .orchestration import (ResearchOrchestrator, RunPlan, configured, plan_summary,
                            resolve_corpus_root)
from .reporting import report, validate_corpus
from .storage import KnowledgeStore
from .analysis import (cohort_report, chronological_splits, first_tranche_report,
                       first_tranche_report_streaming, full_research_report_streaming,
                       ReportProgress)
from .universe import AlpacaAssetMasterClient, universe_report
from .benchmark import acquire_benchmarks, build_benchmark_context


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.trade_intelligence.knowledge")
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build"); build.add_argument("--input", type=Path, required=True); build.add_argument("--output", type=Path, required=True); build.add_argument("--repository-commit", required=True)
    for name in ("status", "validate", "report"):
        command = sub.add_parser(name); command.add_argument("--output", type=Path, default=Path("data/research/trading_knowledge/v1"))
        if name == "report":
            command.add_argument("--group-by", default="")
            command.add_argument("--split", choices=("train", "validation", "test"))
            command.add_argument("--preset", choices=("first-tranche", "full-research"))
            command.add_argument("--report-file", type=Path)
    def plan_args(command):
        command.add_argument("--provider", default="alpaca"); command.add_argument("--feed", default="iex")
        command.add_argument("--start", type=date.fromisoformat, required=True); command.add_argument("--end", type=date.fromisoformat, required=True)
        command.add_argument("--target-per-strategy", type=int, default=5000)
    dry = sub.add_parser("dry-run"); plan_args(dry)
    check = sub.add_parser("provider-check"); check.add_argument("--provider", default="alpaca"); check.add_argument("--feed", default="iex")
    benchmark = sub.add_parser("benchmark-acquire"); benchmark.add_argument("--output", type=Path, required=True); benchmark.add_argument("--start", type=date.fromisoformat, required=True); benchmark.add_argument("--end", type=date.fromisoformat, required=True)
    run = sub.add_parser("run"); plan_args(run); run.add_argument("--input", type=Path); run.add_argument("--symbols", nargs="*", default=()); run.add_argument("--universe", choices=("alpaca-assets",)); run.add_argument("--output-root", type=Path); run.add_argument("--preflight-only", action="store_true"); run.add_argument("--execution-plan-only", action="store_true"); run.add_argument("--execute", action="store_true"); run.add_argument("--repository-commit", default="WORKTREE")
    discover = sub.add_parser("discover"); discover.add_argument("--input", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "dry-run":
        plan = RunPlan(args.provider.upper(), args.feed.upper(), args.start, args.end, args.target_per_strategy)
        print(json.dumps(plan_summary(plan), indent=2)); return 0
    if args.command == "provider-check":
        if args.provider.lower() != "alpaca" or args.feed.lower() != "iex":
            parser.error("only ALPACA/IEX is permitted")
        if not configured(): print(json.dumps({"authenticated": False, "reason": "MISSING_CREDENTIALS"})); return 2
        client = AlpacaHistoricalClient.from_environment(config=AcquisitionConfig())
        try: print(json.dumps(client.health_check(), indent=2))
        finally: client.close()
        return 0
    if args.command == "benchmark-acquire":
        if not configured():
            print(json.dumps({"authenticated": False, "reason": "MISSING_CREDENTIALS"})); return 2
        client = AlpacaHistoricalClient.from_environment(config=AcquisitionConfig(feed="iex", start_date=args.start, end_date=args.end))
        try:
            health = client.health_check()
            result = acquire_benchmarks(client, root=args.output, start=args.start, end=args.end)
        finally:
            client.close()
        result["authentication"] = bool(health.get("authenticated")); result["historical_access"] = bool(health.get("historical_access")); result["feed"] = health.get("feed")
        result["context_database"] = str(build_benchmark_context(args.output, start=args.start, end=args.end))
        print(json.dumps(result, indent=2, default=str)); return 0
    if args.command == "discover":
        with args.input.open(encoding="utf-8") as handle:
            rows = tuple(json.loads(line) for line in handle if line.strip())
        from dataclasses import asdict
        print(json.dumps([asdict(item) for item in discover_candidate_days(rows)], default=str, indent=2)); return 0
    if args.command == "run":
        plan = RunPlan(args.provider.upper(), args.feed.upper(), args.start, args.end, args.target_per_strategy)
        if args.input is None:
            if not args.execute and not args.execution_plan_only:
                print(json.dumps({"run": "PLANNED", "provider_configured": configured(), **plan_summary(plan)}, indent=2)); return 0
            if (not args.symbols and not args.universe) or (not args.execution_plan_only and not configured()):
                print(json.dumps({"run": "STOPPED", "reason": "EXECUTION_REQUIRES_SYMBOLS_AND_CREDENTIALS"}, indent=2)); return 2
            symbols = tuple(args.symbols)
            output_root = args.output_root or Path("data/research/market_data")
            orchestrator = ResearchOrchestrator(plan, root=output_root,
                                                 corpus_root=resolve_corpus_root(output_root))
            if args.universe == "alpaca-assets":
                universe = AlpacaAssetMasterClient.from_environment()
                try:
                    snapshot = universe.snapshot()
                    print(json.dumps({"universe": universe_report(snapshot)}, indent=2))
                    symbols = tuple(item["symbol"] for item in snapshot["assets"] if item.get("included"))
                finally:
                    universe.close()
            if args.execution_plan_only:
                value = orchestrator.execution_plan_only(symbols)
            elif args.preflight_only:
                value = orchestrator.preflight_daily(symbols)
            else:
                value = orchestrator.run_alpaca(symbols, repository_commit=args.repository_commit)
            print(json.dumps(value, indent=2, default=str)); return 0 if not value.get("validation_errors") else 1
        value = ResearchOrchestrator(plan).run_local_mine(args.input, repository_commit=args.repository_commit)
        print(json.dumps(value, indent=2, default=str)); return 0 if not value["validation_errors"] else 1
    if args.command == "build":
        value = build_corpus(JsonlBarProvider(args.input), args.output, repository_commit=args.repository_commit)
        print(json.dumps({"accepted_unique": value.accepted_unique, "strategy_memberships": value.strategy_memberships,
                          "quarantined": value.quarantined, "exact_duplicates": value.exact_duplicates,
                          "near_duplicates": value.near_duplicates, "active_strategies": len(ACTIVE_STRATEGIES)}, indent=2))
    elif args.command == "status":
        corpus_root = resolve_corpus_root(args.output)
        status = status_for_root(args.output)
        status["corpus_root"] = str(corpus_root)
        print(json.dumps(status, indent=2, sort_keys=True))
    elif args.command == "validate":
        errors = validate_corpus(resolve_corpus_root(args.output)); print(json.dumps({"valid": not errors, "errors": errors}, indent=2)); return 0 if not errors else 1
    else:
        if args.preset == "first-tranche":
            store = KnowledgeStore(resolve_corpus_root(args.output), create=False)
            value = first_tranche_report_streaming(lambda: store.iter_episodes(), total=len(store.episode_ids),
                                                   progress=ReportProgress(total=len(store.episode_ids)))
        elif args.preset == "full-research":
            corpus_root = resolve_corpus_root(args.output)
            store = KnowledgeStore(corpus_root, create=False)
            value = full_research_report_streaming(lambda: store.iter_episodes(), total=len(store.episode_ids),
                                                   progress=ReportProgress(total=len(store.episode_ids)),
                                                   daily_context_path=args.output / "candidates" / "candidate_days.jsonl",
                                                   benchmark_context_path=args.output / "benchmark_context" / "benchmark_context.sqlite3")
        elif args.group_by:
            rows = tuple(KnowledgeStore(resolve_corpus_root(args.output), create=False).iter_episodes())
            if args.split:
                rows = tuple(chronological_splits(rows)[args.split.upper()])
            value = cohort_report(rows, tuple(part.strip() for part in args.group_by.split(",") if part.strip()))
        else:
            value = report(resolve_corpus_root(args.output))
        rendered = json.dumps(value, indent=2, sort_keys=True, default=str)
        if args.report_file:
            args.report_file.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(prefix=args.report_file.name + ".", suffix=".tmp",
                                                      dir=args.report_file.parent)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write(rendered); handle.write("\n"); handle.flush(); os.fsync(handle.fileno())
                os.replace(temporary, args.report_file)
            except BaseException:
                try: os.unlink(temporary)
                except OSError: pass
                raise
            print(json.dumps({"report_file": str(args.report_file), "preset": value.get("preset")}, indent=2))
        else:
            print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
