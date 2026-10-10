"""Adapt bounded historical exit replay batches into offline paired comparisons."""
import argparse
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal, localcontext
import hashlib
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from app.asset_modules.engine_catalog import EngineId
from app.asset_modules.strategy_comparison import summarize
from .exit_replay import POLICY_SETS

VERSION = "REPLAY_COMPARISON_ADAPTER_V1"
UNRESOLVED = {"UNRESOLVED_ENTRY_BAR", "UNRESOLVED_MISSING_BARS",
              "UNRESOLVED_INTRABAR_ORDER", "OPEN_AT_DATA_END", "NO_POST_ENTRY_BARS"}
NO_ENTRY = {"NO_ENTRY_MISSING_NEXT_MINUTE", "NO_ENTRY_OPEN_AT_OR_BELOW_STOP",
            "NO_ENTRY_QUANTITY_OUTSIDE_LIMIT"}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()).hexdigest()


def adapt_reports(reports, *, selected_policies=None, champion="STRUCTURAL_STOP_AND_TIME"):
    with localcontext() as context:
        context.prec = 50
        return _adapt_reports(reports, selected_policies=selected_policies, champion=champion)


def _adapt_reports(reports, *, selected_policies, champion):
    if not isinstance(reports, list) or not 1 <= len(reports) <= 500:
        raise ValueError("Provide one to 500 replay batches")
    first = reports[0]
    configuration, source = first["configuration"], first["source"]
    if not isinstance(configuration, dict) or not isinstance(source, dict) or not source:
        raise ValueError("Missing replay provenance")
    policy_set = configuration.get("policy_set", "BASELINE")
    if policy_set not in POLICY_SETS:
        raise ValueError("Unknown policy set")
    version = ("CHRONOLOGICAL_BAR_EXITS_V1" if policy_set == "BASELINE"
               else "EARLY_PARTIAL_BAR_EXITS_V1")
    if configuration.get("policy_version") != version:
        raise ValueError("Unsupported replay version")
    cost = Decimal(configuration["cost_per_share_per_side"])
    if (not cost.is_finite() or cost < 0 or
            type(configuration["hold_minutes"]) is not int or configuration["hold_minutes"] <= 0):
        raise ValueError("Invalid frozen costs or hold duration")
    expected = [p.name for p in POLICY_SETS[policy_set]]
    definitions = json.loads(json.dumps([asdict(p) for p in POLICY_SETS[policy_set]], default=str))
    if "policies" in configuration and configuration["policies"] != definitions:
        raise ValueError("Frozen policy definitions changed")
    chosen = expected if selected_policies is None else selected_policies
    if (not isinstance(chosen, list) or not 2 <= len(chosen) <= len(expected)
            or len(chosen) != len(set(chosen)) or any(p not in expected for p in chosen)
            or champion not in chosen):
        raise ValueError("Select at least two known policies including the champion")
    ranges, partitions, rows, details = [], {}, [], []
    for report in reports:
        if report["configuration"] != configuration or report["source"] != source:
            raise ValueError("Replay source or configuration changed")
        start, end = report["start_byte_offset"], report["next_byte_offset"]
        if type(start) is not int or type(end) is not int or not 0 <= start < end:
            raise ValueError("Invalid replay byte range")
        ranges.append((start, end))
        episodes = report["episodes"]
        if (not isinstance(episodes, list) or len(episodes) > 100
                or report.get("selected_episodes") != len(episodes)):
            raise ValueError("Episode count does not match replay details")
        hashes = report["normalized_partition_hashes"]
        for name, digest in hashes.items():
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError("Invalid partition digest")
            if name in partitions and partitions[name] != digest:
                raise ValueError("Normalized partition changed across batches")
            partitions[name] = digest
        for episode in episodes:
            if (episode["policy_version"] != version
                    or episode["hold_minutes"] != configuration["hold_minutes"]
                    or Decimal(str(episode["cost_per_share_per_side"])) !=
                       Decimal(configuration["cost_per_share_per_side"])):
                raise ValueError("Episode settings differ from frozen configuration")
            at = datetime.fromisoformat(episode["entry_time"])
            if at.tzinfo is None or at.utcoffset() is None:
                raise ValueError("Naive entry timestamp")
            day = at.astimezone(ZoneInfo("America/New_York")).date().isoformat()
            partition = f"{episode['symbol']}_{day}.jsonl"
            if hashes.get(partition) != episode["normalized_sha256"]:
                raise ValueError("Episode partition provenance mismatch")
            results = episode["results"]
            if len(results) != len(expected) or {r["policy"] for r in results} != set(expected):
                raise ValueError("Incomplete or duplicate episode policy results")
            for result in results:
                raw_status = result["status"]
                if raw_status == "CLOSED":
                    if Decimal(str(result["remaining"])) != 0:
                        raise ValueError("Closed replay retains inventory")
                    status = "CLOSED"
                elif raw_status in UNRESOLVED:
                    status = "UNRESOLVED"
                elif raw_status in NO_ENTRY:
                    status = "NO_ENTRY"
                else:
                    raise ValueError("Unknown replay outcome")
                if status != "CLOSED" and result["net_pnl"] is not None:
                    raise ValueError("Unresolved replay has net PnL")
                if result["policy"] in chosen:
                    rows.append({"engine": EngineId.WARRIOR.value,
                                 "policy": f"{version}|{result['policy']}",
                                 "episode_id": episode["episode_id"],
                                 "instrument": episode["symbol"], "trading_date": day,
                                 "status": status, "net_pnl": (
                                     str(Decimal(result["net_pnl"]).normalize()) if status == "CLOSED" else None)})
            details.append({"episode_id": episode["episode_id"],
                            "outcomes": {r["policy"]: r["status"] for r in results}})
    ranges.sort()
    if any(b[0] < a[1] for a, b in zip(ranges, ranges[1:])):
        raise ValueError("Overlapping or duplicated replay batches")
    policy_ids = [f"{version}|{p}" for p in chosen]
    report_digests = sorted(fingerprint(report) for report in reports)
    document = {"version": "ENGINE_COMPARISON_V1",
                "experiment_id": fingerprint({"configuration": configuration, "policies": chosen,
                                               "champion": champion}),
                "dataset_id": fingerprint({"source": source, "partitions": partitions,
                                           "report_digests": report_digests}),
                "cost_model_id": fingerprint({"configuration": configuration}),
                "evidence_kind": "MINUTE_BAR_PROXY",
                "engines": {EngineId.WARRIOR.value: {"policies": policy_ids,
                             "champion": f"{version}|{champion}"}}, "results": rows}
    summary = summarize(document)
    return {"version": VERSION, "comparison_input": document, "summary": summary,
            "provenance": {"batch_count": len(reports), "source": source,
                           "configuration": configuration, "byte_ranges": ranges,
                           "report_digests": report_digests,
                           "noncontiguous_batches": sum(b[0] != a[1] for a, b in zip(ranges, ranges[1:])),
                           "normalized_partition_hashes": partitions},
            "episode_coverage": details,
            "scope": "HISTORICAL_EXIT_MECHANICS_NOT_EXACT_WARRIOR_OR_ACTUAL_FILLS"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report_dir", type=Path)
    parser.add_argument("--policy", action="append", dest="policies")
    parser.add_argument("--champion", default="STRUCTURAL_STOP_AND_TIME")
    parser.add_argument("--output", type=Path, help="New output file; existing files are not overwritten")
    args = parser.parse_args(argv)
    try:
        paths = sorted(args.report_dir.glob("coverage_*.json"))
        if not 1 <= len(paths) <= 500:
            raise ValueError("Expected one to 500 coverage reports")
        reports, bytes_read = [], 0
        for path in paths:
            with path.open("rb") as stream:
                raw = stream.read(20_000_001 - bytes_read)
            bytes_read += len(raw)
            if bytes_read > 20_000_000:
                raise ValueError("Total replay input exceeds 20 MB")
            reports.append(json.loads(raw.decode("utf-8-sig")))
        adapted = adapt_reports(reports, selected_policies=args.policies, champion=args.champion)
        if args.output:
            with args.output.open("x", encoding="utf-8") as output:
                json.dump(adapted, output, indent=2)
        print(json.dumps(adapted["summary"], indent=2))
    except (OSError, ValueError, KeyError, TypeError, ArithmeticError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
