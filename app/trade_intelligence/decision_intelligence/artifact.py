"""Compile the reviewed full-research JSON into a compact offline SQLite DB."""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any

from .models import (
    ARTIFACT_SCHEMA_VERSION, COMPONENT_VERSIONS, CONFIDENCE_STATES,
    EXPECTED_EPISODES, EXPECTED_MEMBERSHIPS, EXPECTED_STRATEGIES,
    REQUIRED_SECTIONS, ArtifactValidationError, ValidationResult,
)

FAILURE_CLASSES = ("STOP_FIRST_5PCT", "LOW_MFE_BELOW_2PCT", "TARGET_2_NOT_REACHED", "TARGET_5_NOT_REACHED", "TARGET_8_NOT_REACHED", "POSITIVE_MFE_BUT_GIVEBACK")
LIMITATIONS = {
    "IEX_SINGLE_EXCHANGE": "Historical benchmark data is an IEX single-exchange research source.",
    "CURRENT_SNAPSHOT_UNIVERSE": "Universe metadata is a current snapshot, not historical point-in-time metadata.",
    "SURVIVORSHIP_BIAS": "The research universe may not represent delisted or unavailable symbols.",
    "ONE_FAILED_CIGL_PARTITION": "One CIGL partition was failed-transient in the source acquisition.",
    "NO_HISTORICAL_CATALYST_NEWS": "Historical catalyst/news evidence is not included in this corpus.",
    "NO_NBBO": "Historical NBBO is unavailable.", "NO_SPREAD": "Historical spread is unavailable.",
    "NO_QUOTE_SIZE": "Historical quote size is unavailable.", "NO_QUEUE_POSITION": "Queue position is unavailable.",
    "NO_LEVEL2": "Historical Level 2 depth is unavailable.", "NO_TRUE_PARTIAL_FILLS": "True partial fills are unavailable.",
    "NO_TRUE_MARKET_IMPACT": "True market impact is unavailable.",
    "BAR_VOLUME_CAPACITY_PROXY_ONLY": "Bar volume capacity is a research proxy, not executable liquidity.",
    "PREMARKET_BENCHMARK_LIMITATION": "Regular-session benchmark semantics may be unavailable before the regular session.",
    "CONTEXT_MISSINGNESS": "Context coverage varies by source field and sufficient history.",
    "TEST_PERIOD_CONCENTRATION": "Some TEST cohorts have concentrated date/month coverage.",
}

def _json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactValidationError(f"cannot read source report: {exc}") from exc

def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None

def _text(v):
    return v if isinstance(v, str) else None

def _j(v):
    return json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

def _metric(obj, *names):
    if not isinstance(obj, dict): return None
    for name in names:
        if name in obj: return obj[name]
    return None

def _split_value(obj, split):
    if not isinstance(obj, dict): return None
    return obj.get(split) or obj.get(split.lower()) or obj.get(split.title())

def _source_check(report, path):
    if not isinstance(report, dict): raise ArtifactValidationError("source report root is not an object")
    missing = [s for s in REQUIRED_SECTIONS if s not in report]
    if missing: raise ArtifactValidationError("missing required report sections: " + ", ".join(missing))
    score = report.get("strategy_scorecards")
    if not isinstance(score, dict) or len(score) != EXPECTED_STRATEGIES:
        raise ArtifactValidationError(f"expected {EXPECTED_STRATEGIES} strategies, got {len(score) if isinstance(score, dict) else 0}")
    meta = report.get("metadata", {})
    if _metric(meta, "records_ingested", "episode_count") not in (EXPECTED_EPISODES, None):
        raise ArtifactValidationError("source episode invariant is incompatible")
    # The report has authoritative top-level counts in different releases; accept
    # the known metadata spellings but never manufacture a count.
    blob = json.dumps(report, separators=(",", ":"))
    if str(EXPECTED_EPISODES) not in blob or str(EXPECTED_MEMBERSHIPS) not in blob:
        raise ArtifactValidationError("source report does not contain required corpus counts")

def _schema(c):
    c.executescript("""
    CREATE TABLE artifact_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE global_evidence (key TEXT PRIMARY KEY, payload_json TEXT NOT NULL);
    CREATE TABLE strategy_evidence (
      strategy TEXT PRIMARY KEY, whole_sample_count INTEGER, train_sample_count INTEGER,
      validation_sample_count INTEGER, test_sample_count INTEGER, median_mfe REAL,
      median_mae REAL, median_max_r REAL, target_2_rate REAL, target_3_rate REAL,
      target_5_rate REAL, target_8_rate REAL, target_10_rate REAL, test_target_2_rate REAL,
      test_target_3_rate REAL, test_target_5_rate REAL, test_target_8_rate REAL,
      test_target_10_rate REAL, stop_first_rate REAL, test_stop_first_rate REAL,
      confidence_state TEXT, walk_forward_state TEXT, policy_stability TEXT,
      descriptive_tier TEXT, concentration_json TEXT, execution_robustness_json TEXT,
      market_regime_robustness_json TEXT, source_json TEXT NOT NULL);
    CREATE TABLE context_evidence (
      strategy TEXT NOT NULL, context_dimension TEXT NOT NULL, context_bucket TEXT NOT NULL,
      sample_count INTEGER, test_sample_count INTEGER, median_mfe REAL, median_mae REAL,
      median_max_r REAL, target_2_rate REAL, target_5_rate REAL, target_8_rate REAL,
      stop_first_rate REAL, confidence_state TEXT, stability_state TEXT, coverage_percent REAL,
      source_json TEXT NOT NULL, PRIMARY KEY(strategy, context_dimension, context_bucket));
    CREATE TABLE progression_evidence (
      strategy TEXT NOT NULL, progression_stage TEXT NOT NULL, target_stage TEXT NOT NULL,
      current_r_bucket TEXT, sample_count INTEGER, confidence_state TEXT, coverage_percent REAL,
      outcome_json TEXT NOT NULL, source_json TEXT NOT NULL,
      PRIMARY KEY(strategy, progression_stage, target_stage, current_r_bucket));
    CREATE TABLE transition_evidence (
      parent_strategy TEXT NOT NULL, child_strategy TEXT NOT NULL, sample_count INTEGER,
      train_count INTEGER, validation_count INTEGER, test_count INTEGER, median_mfe REAL,
      median_mae REAL, median_max_r REAL, target_2_rate REAL, target_3_rate REAL,
      target_5_rate REAL, target_8_rate REAL, target_10_rate REAL, stop_first_rate REAL,
      confidence_state TEXT, stability_state TEXT, source_json TEXT NOT NULL,
      PRIMARY KEY(parent_strategy, child_strategy));
    CREATE TABLE failure_evidence (
      strategy TEXT NOT NULL, failure_class TEXT NOT NULL, context_bucket TEXT NOT NULL,
      sample_count INTEGER, rate REAL, confidence_state TEXT, coverage_percent REAL,
      median_mfe REAL, median_mae REAL, median_max_r REAL, source_json TEXT NOT NULL,
      PRIMARY KEY(strategy, failure_class, context_bucket));
    CREATE TABLE limitations (code TEXT PRIMARY KEY, description TEXT NOT NULL, scope TEXT, severity TEXT, source_json TEXT);
    CREATE INDEX context_lookup ON context_evidence(strategy, context_dimension, context_bucket);
    CREATE INDEX progression_lookup ON progression_evidence(strategy, progression_stage);
    CREATE INDEX transition_lookup ON transition_evidence(parent_strategy, child_strategy);
    CREATE INDEX failure_lookup ON failure_evidence(strategy, failure_class);
    """)

def _target_fields(data, target, prefix=""):
    x = data.get(str(target), {}) if isinstance(data, dict) else {}
    return x if isinstance(x, dict) else {}

def _build_rows(c, report):
    score = report["strategy_scorecards"]; profit = report.get("profit_research", {}); temporal = report.get("temporal", {})
    for strategy, s in score.items():
        rates = s.get("target_hit_rates", {}); stops = s.get("stop_first_rates", {})
        test = _split_value(temporal.get("strategy_scorecards", {}).get(strategy), "TEST") or _split_value(temporal.get("execution_analysis", {}).get(strategy), "TEST") or {}
        tr = _split_value(temporal.get("strategy_scorecards", {}).get(strategy), "TRAIN") or {}
        va = _split_value(temporal.get("strategy_scorecards", {}).get(strategy), "VALIDATION") or {}
        vals = [strategy, s.get("sample_count"), tr.get("sample_count"), va.get("sample_count"), test.get("sample_count"), s.get("median_mfe"), s.get("median_mae"), s.get("median_maximum_r"), *(rates.get(str(x)) for x in (2,3,5,8,10)), *(test.get("target_hit_rates",{}).get(str(x)) for x in (2,3,5,8,10)), stops.get("5"), test.get("stop_first_rates",{}).get("5"), s.get("confidence_state"), None, None, None, _j(s.get("concentration")), None, None, _j(s)]
        c.execute("INSERT INTO strategy_evidence VALUES (" + ",".join("?" for _ in range(28)) + ")", vals)
        for kind, mapping in (("percent_target", profit.get(strategy, {}).get("percent_targets", {})), ("r_target", profit.get(strategy, {}).get("r_targets", {}))):
            for bucket, row in mapping.items() if isinstance(mapping, dict) else ():
                c.execute("INSERT OR REPLACE INTO context_evidence VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (strategy, kind, str(bucket), row.get("sample_count"), None, row.get("median_mfe"), row.get("median_mae"), row.get("median_maximum_r"), row.get("hit_rate") if kind=="percent_target" else None, None, None, row.get("stop_first_rate"), row.get("confidence_state"), None, None, _j(row)))
        runner = report.get("runner_research", {}).get(strategy)
        if runner: c.execute("INSERT INTO progression_evidence VALUES (?,?,?,?,?,?,?,?,?)", (strategy,"RUNNER","POST_TARGET_8",None,runner.get("sample_count"),None,None,_j(runner),_j(runner)))
    for row in report.get("reentry", {}).get("transition_matrix", []):
        if not isinstance(row, dict): continue
        c.execute("INSERT OR REPLACE INTO transition_evidence VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (row.get("parent_strategy"),row.get("child_strategy"),row.get("sample_count"),None,None,None,row.get("median_mfe"),row.get("median_mae"),row.get("median_maximum_r"),row.get("target_hit_rates",{}).get("2"),row.get("target_hit_rates",{}).get("3"),row.get("target_hit_rates",{}).get("5"),row.get("target_hit_rates",{}).get("8"),row.get("target_hit_rates",{}).get("10"),row.get("stop_first_rates",{}).get("5"),row.get("confidence_state"),None,_j(row)))
    for dim, value in report.get("context_analysis", {}).items():
        if not isinstance(value, dict): continue
        buckets = value.get("buckets")
        if isinstance(buckets, dict):
            for bucket, row in buckets.items():
                if isinstance(row, dict): c.execute("INSERT OR REPLACE INTO context_evidence VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ("__ALL__",dim,str(bucket),row.get("sample_count"),row.get("test_sample_count"),row.get("median_mfe"),row.get("median_mae"),row.get("median_maximum_r"),row.get("target_2_rate"),row.get("target_5_rate"),row.get("target_8_rate"),row.get("stop_first_rate"),row.get("confidence_state"),row.get("stability_state"),row.get("coverage_percent"),_j(row)))
        elif value.get("status") or value.get("coverage_percent") is not None:
            c.execute("INSERT OR REPLACE INTO context_evidence VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ("__ALL__",dim,"__SUMMARY__",value.get("available_count"),None,None,None,None,None,None,None,None,None,None,value.get("coverage_percent"),_j(value)))
    for failure, value in report.get("failure_analysis", {}).get("classes", {}).items():
        if isinstance(value, dict) and isinstance(value.get("by_strategy"), dict):
            for strategy,row in value["by_strategy"].items():
                c.execute("INSERT OR REPLACE INTO failure_evidence VALUES (?,?,?,?,?,?,?,?,?,?,?)", (strategy,failure,"__SUMMARY__",row.get("sample_count"),row.get("share_of_population"),row.get("confidence_state"),None,row.get("median_mfe"),row.get("median_mae"),row.get("median_maximum_r"),_j(row)))
        elif isinstance(value, dict): c.execute("INSERT OR REPLACE INTO failure_evidence VALUES (?,?,?,?,?,?,?,?,?,?,?)", ("__ALL__",failure,"__SUMMARY__",value.get("sample_count"),value.get("share_of_population"),value.get("confidence_state"),None,value.get("median_mfe"),value.get("median_mae"),value.get("median_maximum_r"),_j(value)))

def _metadata(c, report, source_path, source_hash, source_size, baseline, builder_commit, command):
    effective_builder = builder_commit or baseline
    meta = {"artifact_schema_version":ARTIFACT_SCHEMA_VERSION,"generated_at":_dt.datetime.now(_dt.timezone.utc).isoformat(),"source_report_path":str(source_path),"source_report_sha256":source_hash,"source_report_size":str(source_size),"source_report_baseline":baseline,"artifact_build_baseline":baseline,"source_episode_count":str(EXPECTED_EPISODES),"source_membership_count":str(EXPECTED_MEMBERSHIPS),"strategy_count":str(EXPECTED_STRATEGIES),"strategy_taxonomy":_j(sorted(report["strategy_scorecards"])),"builder_commit":effective_builder or "unknown","generation_command":command,"research_component_versions":_j(COMPONENT_VERSIONS),"preset":report.get("preset",""),"source_metadata":_j(report.get("metadata",{}))}
    c.executemany("INSERT INTO artifact_metadata VALUES (?,?)", meta.items())
    c.executemany("INSERT INTO global_evidence VALUES (?,?)", (("execution_research", _j(report.get("execution_research", {}))), ("context_analysis", _j(report.get("context_analysis", {}))), ("profit_research", _j(report.get("profit_research", {}))), ("r_multiple_research", _j(report.get("r_multiple_research", {}))), ("partial_exit_research", _j(report.get("partial_exit_research", {})))))
    source_limits = report.get("limitations", [])
    for code, desc in LIMITATIONS.items(): c.execute("INSERT INTO limitations VALUES (?,?,?,?,?)", (code,desc,"research artifact","HIGH" if code in {"NO_NBBO","NO_SPREAD","NO_LEVEL2","NO_HISTORICAL_CATALYST_NEWS"} else "MEDIUM",_j(source_limits)))

def validate_artifact(path, *, source_report_path=None, read_only=False):
    uri = f"file:{Path(path).resolve().as_posix()}?mode=ro" if read_only else str(path)
    try: c=sqlite3.connect(uri, uri=read_only)
    except sqlite3.Error as exc: return ValidationResult(False,(str(exc),))
    errors=[]
    try:
        tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}; required={"artifact_metadata","global_evidence","strategy_evidence","context_evidence","progression_evidence","transition_evidence","failure_evidence","limitations"}; errors += [f"missing table {x}" for x in sorted(required-tables)]
        md=dict(c.execute("SELECT key,value FROM artifact_metadata")) if "artifact_metadata" in tables else {}
        if md.get("artifact_schema_version") != ARTIFACT_SCHEMA_VERSION: errors.append("invalid artifact schema version")
        if int(md.get("strategy_count",0)) != EXPECTED_STRATEGIES: errors.append("invalid strategy count")
        if c.execute("SELECT COUNT(*) FROM strategy_evidence").fetchone()[0] != EXPECTED_STRATEGIES: errors.append("strategy evidence is incomplete")
        if "global_evidence" in tables and {r[0] for r in c.execute("SELECT key FROM global_evidence")} != {"execution_research","context_analysis","profit_research","r_multiple_research","partial_exit_research"}: errors.append("global evidence is incomplete")
        if not c.execute("SELECT 1 FROM limitations LIMIT 1").fetchone(): errors.append("limitations absent")
        if source_report_path:
            h=hashlib.sha256(Path(source_report_path).read_bytes()).hexdigest()
            if h != md.get("source_report_sha256"): errors.append("source report SHA-256 mismatch")
        for (v,) in c.execute("SELECT confidence_state FROM strategy_evidence WHERE confidence_state IS NOT NULL"):
            if v not in CONFIDENCE_STATES: errors.append(f"invalid confidence state {v}")
    except sqlite3.Error as exc: errors.append(str(exc))
    finally: c.close()
    return ValidationResult(not errors,tuple(errors))

def build_artifact(source_report_path, output_path, *, baseline="unknown", builder_commit=None, generation_command="DI-1 build"):
    source=Path(source_report_path); output=Path(output_path); report=_json(source); _source_check(report,source)
    digest=hashlib.sha256(source.read_bytes()).hexdigest(); output.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix=f".{output.name}.",suffix=".tmp",dir=output.parent); os.close(fd)
    try:
        c=sqlite3.connect(tmp); _schema(c); _metadata(c,report,source,digest,source.stat().st_size,baseline,builder_commit,generation_command); _build_rows(c,report); c.commit(); c.close()
        validate_artifact(tmp,source_report_path=source).raise_if_invalid(); os.replace(tmp,output)
        return validate_artifact(output,source_report_path=source,read_only=True).raise_if_invalid()
    except Exception:
        try: os.unlink(tmp)
        except OSError: pass
        raise

def main(argv=None):
    p=argparse.ArgumentParser(description="Offline DI-1 artifact builder/inspector"); sub=p.add_subparsers(dest="command",required=True)
    b=sub.add_parser("build"); b.add_argument("report"); b.add_argument("output"); b.add_argument("--baseline",default="unknown")
    v=sub.add_parser("validate"); v.add_argument("artifact"); v.add_argument("--source-report")
    m=sub.add_parser("metadata"); m.add_argument("artifact")
    s=sub.add_parser("strategy"); s.add_argument("artifact"); s.add_argument("name")
    x=sub.add_parser("context"); x.add_argument("artifact"); x.add_argument("strategy"); x.add_argument("dimension")
    t=sub.add_parser("transition"); t.add_argument("artifact"); t.add_argument("parent"); t.add_argument("child")
    l=sub.add_parser("limitations"); l.add_argument("artifact")
    args=p.parse_args(argv)
    if args.command=="build": build_artifact(args.report,args.output,baseline=args.baseline,builder_commit=args.baseline); print(args.output)
    elif args.command=="validate": validate_artifact(args.artifact,source_report_path=args.source_report,read_only=True).raise_if_invalid(); print("VALID")
    else:
        with sqlite3.connect(f"file:{Path(args.artifact).resolve().as_posix()}?mode=ro",uri=True) as c:
            if args.command == "metadata": rows=c.execute("SELECT key,value FROM artifact_metadata")
            elif args.command == "strategy": rows=c.execute("SELECT * FROM strategy_evidence WHERE strategy=?", (args.name,))
            elif args.command == "context": rows=c.execute("SELECT * FROM context_evidence WHERE strategy=? AND context_dimension=?", (args.strategy,args.dimension))
            elif args.command == "transition": rows=c.execute("SELECT * FROM transition_evidence WHERE parent_strategy=? AND child_strategy=?", (args.parent,args.child))
            else: rows=c.execute("SELECT * FROM limitations ORDER BY code")
            print(json.dumps([list(row) for row in rows], indent=2, default=str))
    return 0

if __name__ == "__main__": sys.exit(main())
