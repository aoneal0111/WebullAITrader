"""Offline CLI: ``python -m app.trade_intelligence.knowledge``."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .mining import JsonlBarProvider, build_corpus
from .models import ACTIVE_STRATEGIES
from .reporting import report, validate_corpus
from .storage import KnowledgeStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.trade_intelligence.knowledge")
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build"); build.add_argument("--input", type=Path, required=True); build.add_argument("--output", type=Path, required=True); build.add_argument("--repository-commit", required=True)
    for name in ("status", "validate", "report"):
        command = sub.add_parser(name); command.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "build":
        value = build_corpus(JsonlBarProvider(args.input), args.output, repository_commit=args.repository_commit)
        print(json.dumps({"accepted_unique": value.accepted_unique, "strategy_memberships": value.strategy_memberships,
                          "quarantined": value.quarantined, "exact_duplicates": value.exact_duplicates,
                          "near_duplicates": value.near_duplicates, "active_strategies": len(ACTIVE_STRATEGIES)}, indent=2))
    elif args.command == "status": print(json.dumps(KnowledgeStore(args.output, create=False).status(), indent=2, sort_keys=True))
    elif args.command == "validate":
        errors = validate_corpus(args.output); print(json.dumps({"valid": not errors, "errors": errors}, indent=2)); return 0 if not errors else 1
    else: print(json.dumps(report(args.output), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
