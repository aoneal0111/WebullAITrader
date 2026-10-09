"""Export bounded closed Warrior captures and nearby entry evidence, read-only."""
from __future__ import annotations

import argparse
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import sqlite3
import time
import zipfile

from .capture_profit_audit import audit_capture, instant, order_key

CONTEXT_TYPES = (
    "DECISION", "DI_ENTRY_DIAGNOSTIC", "EXECUTION_GATE_DECISION",
    "SETUP_LIFECYCLE", "STATE_TRANSITION", "SPREAD_EVIDENCE", "DATA_QUALITY",
)


@contextmanager
def read_database(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    try:
        deadline = time.monotonic() + 10
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        db.execute("PRAGMA query_only=ON")
        yield db
    finally:
        db.close()


def bounded_rows(db, query, args, limit, byte_limit):
    rows, size = [], 0
    for row in db.execute(query, args):
        size += sum(len(str(v).encode("utf-8")) for v in row)
        if len(rows) >= limit or size > byte_limit:
            raise ValueError("DATABASE_RESULT_LIMIT")
        rows.append(row)
    return rows


def export_batch(root, run_start, *, offset=0, limit=5, output_parent=None):
    if not isinstance(offset, int) or offset < 0 or not isinstance(limit, int) or not 1 <= limit <= 5:
        raise ValueError("INVALID_BATCH_BOUNDS")
    start = instant(run_start).astimezone(timezone.utc)
    root = Path(root)
    with read_database(root / "data/sandbox/paper-execution.sqlite3") as db:
        row = db.execute("SELECT value FROM metadata WHERE key='active_campaign_id'").fetchone()
        if row is None:
            raise ValueError("ACTIVE_CAMPAIGN_MISSING")
        campaign = row[0]
        raw_orders = bounded_rows(db, """SELECT payload FROM orders
            WHERE COALESCE(json_extract(payload,'$.paper_campaign_id'),
                           'legacy-paper-campaign')=? LIMIT 5001""",
            (campaign,), 5000, 32_000_000)
    grouped = defaultdict(list)
    for (raw,) in raw_orders:
        order = json.loads(raw)
        life = order_key(order)[1]
        if str(life).startswith("WARRIOR_MOMENTUM_V1|"):
            grouped[life].append(order)
    eligible = []
    for life, orders in grouped.items():
        buys = [o for o in orders if o["request"]["side"] == "BUY"
                and Decimal(o["filled_quantity"]) > 0]
        if not buys:
            continue
        created = min(instant(o["created_at"]) for o in buys).astimezone(timezone.utc)
        net = sum(Decimal(o["filled_quantity"]) * (1 if o["request"]["side"] == "BUY" else -1)
                  for o in orders)
        if created < start or net or any(o["status"] not in {"FILLED", "CANCELLED", "REJECTED", "EXPIRED"}
                                       for o in orders):
            continue
        eligible.append((created, life, orders))
    eligible.sort(key=lambda item: (item[0], item[1]))
    chosen = eligible[offset:offset + limit]
    parent = Path(output_parent) if output_parent is not None else root / "data/research/warrior_capture_audit_v1"
    output = parent / ("broader_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f"))
    output.mkdir(parents=True, exist_ok=False)
    manifest = {"run_start": start.isoformat(), "campaign": campaign,
                "eligible_closed": len(eligible), "offset": offset, "limit": limit,
                "selection": "CLOSED_WARRIOR_ENTRY_TIME_ORDER_NOT_PNL_RANKED",
                "audit_settings": {"activation_r": "0.40", "peak_retention": "0.50",
                                   "max_quote_age_seconds": "5", "extra_cost_per_share_per_side": "0"},
                "scope": "RETROSPECTIVE_CLOSED_TRADE_BATCH_NOT_FORWARD_VALIDATION",
                "exports": [], "excluded": []}
    for index, (created, life, orders) in enumerate(chosen, offset + 1):
        symbol = orders[0]["request"]["symbol"]
        try:
            if len(orders) > 500:
                raise ValueError("ORDER_LIMIT")
            with read_database(root / "data/warrior_momentum_v1_forward/forward_capture.sqlite3") as db:
                paths = bounded_rows(db, """SELECT timestamp,symbol,payload_json FROM capture_records
                    WHERE symbol=? AND record_type='EXECUTION_PRICE_PATH'
                    AND json_extract(payload_json,'$.lifecycle_id')=?
                    AND COALESCE(json_extract(payload_json,'$.paper_campaign_id'),
                                 'legacy-paper-campaign')=? ORDER BY sequence LIMIT 4001""",
                    (symbol, life, campaign), 4000, 8_000_000)
            if not paths:
                raise ValueError("NO_CAPTURE_RECORDS")
            data = {"run_start": start.isoformat(), "selection": manifest["selection"],
                    "orders": orders, "capture_records": [
                        {"timestamp": t, "symbol": s, "payload": json.loads(p)} for t, s, p in paths]}
            # These are nearby symbol records, not claimed exact authorization
            # joins. Older records may omit campaign or generation identity.
            with read_database(root / "data/warrior_momentum_v1_forward/forward_capture.sqlite3") as db:
                marks = ",".join("?" for _ in CONTEXT_TYPES)
                contexts = bounded_rows(db, f"""SELECT timestamp,symbol,record_type,payload_json
                    FROM capture_records WHERE symbol=? AND timestamp>=? AND timestamp<=?
                    AND record_type IN ({marks}) ORDER BY timestamp,sequence LIMIT 1001""",
                    (symbol, (created - timedelta(minutes=5)).isoformat(),
                     (created + timedelta(seconds=30)).isoformat(), *CONTEXT_TYPES), 1000, 4_000_000)
            data["entry_context"] = {"scope": "NEARBY_SYMBOL_TIME_RECORDS_NOT_PROVEN_ORDER_LINKAGE",
                "window_start": (created - timedelta(minutes=5)).isoformat(),
                "window_end": (created + timedelta(seconds=30)).isoformat(),
                "records": [{"timestamp": t, "symbol": s, "record_type": typ,
                             "payload": json.loads(p)} for t, s, typ, p in contexts]}
            report = audit_capture(data, activation_r=Decimal("0.40"),
                                   peak_retention=Decimal("0.50"),
                                   max_quote_age_seconds=Decimal("5"),
                                   extra_cost_per_side=Decimal("0"))
            raw = json.dumps(data, separators=(",", ":")).encode("utf-8")
            if len(raw) > 8_000_000:
                raise ValueError("EXPORT_BYTE_LIMIT")
            filename = f"batch_{index}_capture.json"
            (output / filename).write_bytes(raw)
            manifest["exports"].append({"file": filename, "symbol": symbol, "lifecycle": life,
                "capture_records": len(paths), "entry_context_records": len(contexts),
                "audit": report})
        except (ValueError, sqlite3.OperationalError) as error:
            manifest["excluded"].append({"symbol": symbol, "lifecycle": life,
                                         "reason": str(error)})
    (output / "manifest.json").write_text(json.dumps(manifest, default=str, indent=2), encoding="utf-8")
    archive = output.with_suffix(".zip")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as target:
        for path in sorted(output.iterdir()):
            target.write(path, path.name)
    return manifest, archive


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args(argv)
    with args.snapshot.open("rb") as source:
        raw = source.read(8_000_001)
    if len(raw) > 8_000_000:
        parser.error("Snapshot exceeds 8 MB")
    snapshot = json.loads(raw.decode("utf-8-sig"))
    manifest, archive = export_batch(args.root, snapshot["runtime_started_at"],
                                   offset=args.offset, limit=args.limit)
    print("RUN_START:", manifest["run_start"], "ELIGIBLE_CLOSED:", manifest["eligible_closed"])
    for item in manifest["exports"]:
        for r in item["audit"]["lifecycles"]:
            print(json.dumps({"symbol": r["symbol"], "lifecycle": r["lifecycle"],
                "status": r["status"], "actual_pnl": r.get("actual_closed_pnl"),
                "sampled_peak": (r.get("sampled_peak_total_profit") or {}).get("total_profit_mark"),
                "signal": r.get("first_observed_signal"), "problems": r["reconciliation_problems"],
                "entry_context_records": item["entry_context_records"]}, default=str))
    for item in manifest["excluded"]:
        print("EXCLUDED:", json.dumps(item))
    print("UPLOAD_FILE:", archive)
    if not manifest["exports"]:
        print("NO_EXPORTED_LIFECYCLES")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
