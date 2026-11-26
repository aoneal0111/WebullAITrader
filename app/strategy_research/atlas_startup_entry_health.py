"""Bounded offline Atlas readiness and entry-funnel diagnostic.

Observes existing snapshot fields only. A five-minute service-level objective is
reported as PASSED, EXCEEDED, INCOMPLETE or UNMEASURABLE; no startup is initiated,
trading policy changed, or failed execution gate automatically bypassed.
"""
from __future__ import annotations

import argparse
from datetime import datetime, UTC
import json
from pathlib import Path

SLA_SECONDS = 300
MAX_BYTES = 2_000_000
STAGES = (
    "process_started", "gui_ready", "runtime_started", "broker_connect_started",
    "broker_connected", "stream_connect_started", "transport_connected",
    "registration_ready", "universe_refresh_started", "universe_refresh_completed",
    "reference_warmup_started", "reference_warmup_completed",
    "subscription_requested", "subscription_completed",
    "first_raw_callback", "first_callback_dequeued",
    "first_payload_decode_success", "first_normalized_market_event",
    "first_scanner_ingestion", "first_scanner_evaluation", "scanner_active",
    "feed_healthy",
)
FUNNEL = (
    "SCANNER_QUALIFIED", "SETUP_FORMING", "SETUP_TRIGGERED", "TECHNICAL_SIGNAL",
    "EXECUTION_QUOTE_REQUESTED", "EXECUTION_QUOTE_RETURNED",
    "EXECUTION_QUOTE_ACCEPTED", "ORDER_INTENT", "ORDER_SUBMITTED",
    "ORDER_ACKNOWLEDGED", "FILL",
)


def aware(value):
    if value is None:
        return None
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.utcoffset() is None:
        raise ValueError("STARTUP_TIMESTAMP_MUST_BE_AWARE")
    return dt.astimezone(UTC)


def _startups(snapshot):
    """Recover the durable run baseline without inventing a GUI-ready stage.

    PerformanceDiagnostics stores its authoritative *run* timestamp at the
    artifact top level. Older snapshots did not also emit the optional
    startup.process_started_at stage, which made valid reports UNMEASURABLE.
    This is a run start, not proof of OS process launch.
    """
    data = {}
    if isinstance(snapshot.get("startup"), dict):
        data = dict(snapshot["startup"])
    elif isinstance(snapshot.get("startup_metrics"), dict):
        data = dict(snapshot["startup_metrics"])
    elif isinstance(snapshot.get("metrics"), dict):
        metrics = snapshot["metrics"]
        data = {k.removeprefix("startup_"): v for k, v in metrics.items()
                if k.startswith("startup_")}
    if not data.get("process_started_at") and snapshot.get("process_started_at"):
        data["process_started_at"] = snapshot["process_started_at"]
        data["_process_start_basis"] = "durable_run_start"
    return data


def startup_review(snapshot, *, checked_at=None):
    if not isinstance(snapshot, dict):
        raise ValueError("SNAPSHOT_OBJECT_REQUIRED")
    data = _startups(snapshot)
    ticks = {s: aware(data.get(f"{s}_at")) for s in STAGES}
    start = ticks["process_started"] or ticks["runtime_started"]
    if not start:
        return {"status": "UNMEASURABLE", "reason": "NO_PROCESS_OR_RUNTIME_START_TIMESTAMP",
                "sla_seconds": SLA_SECONDS, "durations_seconds": {}, "stages_missing": list(STAGES)}
    observed = [(s, (stamp - start).total_seconds()) for s, stamp in ticks.items()
                if stamp is not None]
    if any(value < 0 for _, value in observed):
        raise ValueError("STARTUP_CLOCK_ORDER_INVALID")
    # Avoid treating an archived screenshot as an *ongoing* startup; elapsed is
    # only computed when a caller supplies an explicit observation time.
    now = aware(checked_at) if checked_at is not None else None
    if now is not None and now < start:
        raise ValueError("OBSERVATION_PRECEDES_START")
    gui = ticks["gui_ready"]
    if gui is not None:
        gui_duration = (gui - start).total_seconds()
        gui_status = "PASSED" if gui_duration <= SLA_SECONDS else "EXCEEDED"
    elif now is not None:
        gui_duration = (now - start).total_seconds()
        gui_status = "EXCEEDED" if gui_duration > SLA_SECONDS else "INCOMPLETE"
    else:
        gui_duration = None
        gui_status = "INCOMPLETE"
    ready = ticks["feed_healthy"]
    if ready is not None:
        duration = (ready - start).total_seconds()
        state = "PASSED" if duration <= SLA_SECONDS else "EXCEEDED"
    elif now is not None:
        duration = (now - start).total_seconds()
        state = "EXCEEDED" if duration > SLA_SECONDS else "INCOMPLETE"
    else:
        duration = None
        state = "INCOMPLETE"
    boundaries = []
    previous_stage = None
    previous_at = None
    for stage in STAGES:
        stamp = ticks[stage]
        if stamp is None:
            continue
        if previous_at is not None:
            delta = (stamp - previous_at).total_seconds()
            if delta >= 0:
                boundaries.append({"from": previous_stage, "to": stage,
                                   "seconds": round(delta, 3)})
        previous_stage, previous_at = stage, stamp
    boundaries.sort(key=lambda row: row["seconds"], reverse=True)
    return {
        "status": state, "sla_seconds": SLA_SECONDS,
        "gui_status": gui_status,
        "gui_observed_seconds": None if gui_duration is None else round(gui_duration, 3),
        "observed_seconds": None if duration is None else round(duration, 3),
        "start_basis": (data.get("_process_start_basis") or
                        ("startup_process_started" if ticks["process_started"] else "runtime_started")),
        "ready_basis": "feed_healthy", "gui_ready_at": data.get("gui_ready_at"),
        "runtime_started_at": data.get("runtime_started_at"),
        "feed_healthy_at": data.get("feed_healthy_at"),
        "longest_observed_stage_intervals": boundaries[:8],
        "stages_missing": [s for s in STAGES if ticks[s] is None],
        "caveat": ("GUI startup and feed readiness are separate five-minute assessments; "
                   "feed_healthy is stronger and may depend on market session conditions"),
    }


def entry_review(snapshot):
    source = snapshot.get("entry_conversion")
    if not isinstance(source, dict):
        return {"status": "NO_ENTRY_CONVERSION_EVIDENCE", "stage_counts": {},
                "unproven": "No scanner-to-order causal joining possible"}
    raw = source.get("funnel_counts")
    if not isinstance(raw, dict):
        return {"status": "NO_FUNNEL_COUNTS", "stage_counts": {},
                "unproven": "Recorded counts are not trade-level matched events"}
    counts = {}
    for name in FUNNEL:
        value = raw.get(name)
        if value is None:
            continue
        if type(value) is not int or value < 0:
            raise ValueError("INVALID_ENTRY_FUNNEL_COUNTER")
        counts[name] = value
    # Different stages may count repeated assessments or distinct objects.
    # Do not use ratios/deltas as a causal conversion rate.
    gates = source.get("execution_gate_counters")
    return {
        "status": "OBSERVED_COUNTS_ONLY",
        "stage_counts": counts,
        "execution_gate_counters": (
            {str(k): v for k, v in list(gates.items())[:32]
             if type(v) is int and v >= 0}
            if isinstance(gates, dict) else {}),
        "unproven": "Funnel counters have different denominators; not order-fill conversion or causal rejection",
    }


def audit(snapshot, *, checked_at=None):
    return {
        "scope": "READ_ONLY_ATLAS_STARTUP_ENTRY_HEALTH_V1",
        "startup": startup_review(snapshot, checked_at=checked_at),
        "entry": entry_review(snapshot),
        "paper_only": True,
        "orders": 0, "network_requests": 0, "runtime_modified": False,
        "limitations": [
            "Only already captured local metrics are examined; no Windows process measured remotely",
            "No automatic bypass of execution-quality, safety, risk, or session gates",
            "No profitability, win-rate, or five-minute startup guarantee without measured evidence",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--checked-at", help="Optional aware ISO-8601 timestamp for still-running startup")
    args = parser.parse_args()
    if args.snapshot.is_symlink() or not args.snapshot.is_file() or args.snapshot.stat().st_size > MAX_BYTES:
        raise ValueError("BOUNDED_SNAPSHOT_REQUIRED")
    data = json.loads(args.snapshot.read_text(encoding="utf-8-sig"))
    result = audit(data, checked_at=args.checked_at)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)
    print("STARTUP_STATUS=" + result["startup"]["status"])
    print("ENTRY_STATUS=" + result["entry"]["status"])
    print("REPORT=" + str(args.output))


if __name__ == "__main__":
    main()
