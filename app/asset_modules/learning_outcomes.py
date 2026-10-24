"""Read-only equity PAPER fill reconciliation for observational learning memory."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
from hashlib import sha256
import json
from pathlib import Path

from app.asset_modules.engine_catalog import lifecycle_owner
from app.read_only_sqlite import read_database, bounded_rows

FIELDS = {"campaign", "engine", "lifecycle", "instrument", "policy_version",
          "opened_at", "closed_at", "quantity", "net_pnl", "recorded_fees",
          "evidence_kind", "cost_scope", "source_kind"}


def amount(value):
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError("Decimal strings required")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("Invalid amount") from exc
    if not result.is_finite() or abs(result) > Decimal('1e12') or not -12 <= result.as_tuple().exponent <= 12:
        raise ValueError("Amount outside bounds")
    return result


def instant(value):
    at = datetime.fromisoformat(value)
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("Aware timestamp required")
    return at.astimezone(timezone.utc)


def validate_outcome(value):
    if not isinstance(value, dict) or set(value) not in (FIELDS, FIELDS | {"strategy_family"}):
        raise ValueError("Invalid outcome fields")
    for name in FIELDS - {"quantity", "net_pnl", "recorded_fees"}:
        if not isinstance(value[name], str) or not 1 <= len(value[name]) <= 512:
            raise ValueError("Invalid outcome label")
    if "strategy_family" in value and (not isinstance(value["strategy_family"], str)
            or not 1 <= len(value["strategy_family"]) <= 101):
        raise ValueError("Invalid strategy family")
    if value["engine"] not in {"WARRIOR_MOMENTUM_V1", "QUICK_SCALPER", "UNKNOWN"}:
        raise ValueError("Equity outcome owner required")
    if (value["evidence_kind"] != "DURABLE_EQUITY_PAPER_FILLS"
            or value["cost_scope"] != "RECORDED_COMMISSIONS_NO_EXTRA_COST_ASSUMED"
            or value["source_kind"] != "UNVERIFIED_PAPER_LEDGER_PROVENANCE"):
        raise ValueError("Explicit equity PAPER evidence scope required")
    if amount(value["quantity"]) <= 0 or amount(value["recorded_fees"]) < 0:
        raise ValueError("Invalid outcome quantity or fees")
    amount(value["net_pnl"])
    if instant(value["closed_at"]) < instant(value["opened_at"]):
        raise ValueError("Outcome exit precedes entry")
    identity = [value[k] for k in ("campaign", "lifecycle", "instrument")]
    return sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()


def read_equity_outcomes(path):
    with read_database(path) as db:
        db.execute("BEGIN")  # One consistent source snapshot; no source writes.
        row = db.execute("SELECT value FROM metadata WHERE key='active_campaign_id'").fetchone()
        if not row:
            raise ValueError("Active campaign missing")
        campaign = row[0]
        rows = bounded_rows(db, """SELECT payload FROM orders
            WHERE COALESCE(json_extract(payload,'$.paper_campaign_id'),'legacy-paper-campaign')=?
            ORDER BY order_id LIMIT 5001""", (campaign,), 5000, 32_000_000)
    groups = defaultdict(list)
    for (raw,) in rows:
        order = json.loads(raw)
        r = order["request"]
        # Missing lifecycle cannot safely join independent buy and sell orders.
        life = r.get("strategy_lifecycle_id")
        groups[(life or "MISSING:" + order["order_id"], r["symbol"])].append(order)
    excluded = Counter()
    outcomes, seen_fills = [], set()
    with localcontext() as ctx:
        ctx.prec = 50
        for (life, symbol), orders in sorted(groups.items()):
            if life.startswith("MISSING:"):
                excluded["MISSING_LIFECYCLE"] += 1
                continue
            buys, sells, fees = [], [], Decimal(0)
            owners, versions, families = set(), set(), set()
            for order in orders:
                r, fills = order["request"], order["fills"]
                if r["side"] not in {"BUY", "SELL"}:
                    raise ValueError("Unsupported equity side")
                total = Decimal(0)
                for fill in fills:
                    identity = fill["fill_id"]
                    if identity in seen_fills:
                        raise ValueError("Duplicate durable fill")
                    seen_fills.add(identity)
                    q, price, fee = (amount(fill[k]) for k in ("quantity", "price", "commission"))
                    if q <= 0 or price <= 0 or fee < 0:
                        raise ValueError("Invalid durable fill")
                    total += q
                    fees += fee
                    (buys if r["side"] == "BUY" else sells).append((instant(fill["timestamp"]), q, price))
                if total != amount(order["filled_quantity"]):
                    raise ValueError("Filled quantity differs from durable fills")
                if fills and r["side"] == "BUY":
                    m = r.get("metadata") or {}
                    owners.add(m.get("strategy_owner"))
                    # Controller policy_version can be a per-order metadata hash;
                    # it is not a comparable frozen strategy version.
                    versions.add(m.get("strategy_policy_version") or "UNKNOWN")
                    families.add(m.get("strategy_family"))
            if not buys and not sells:
                excluded["NO_FILLS"] += 1
                continue
            if any(o["status"] not in {"FILLED", "CANCELLED", "REJECTED", "EXPIRED"} for o in orders):
                excluded["NONTERMINAL_ORDERS"] += 1
                continue
            bq = sum((x[1] for x in buys), Decimal(0))
            sq = sum((x[1] for x in sells), Decimal(0))
            if not buys or bq != sq:
                excluded["UNBALANCED_FILLS"] += 1
                continue
            # Reject re-entry/interleaved inventories: a simple lifecycle cash
            # difference is not an episode boundary in those cases.
            inventory = Decimal(0)
            timeline = sorted([(t, 0, q) for t, q, _ in buys] + [(t, 1, -q) for t, q, _ in sells])
            if any((inventory := inventory + q) <= 0 for _, _, q in timeline[:-1]):
                excluded["AMBIGUOUS_INVENTORY_SEQUENCE"] += 1
                continue
            owner = lifecycle_owner(life)
            if owner and owner.value not in {"WARRIOR_MOMENTUM_V1", "QUICK_SCALPER"}:
                excluded["NON_EQUITY_OWNER"] += 1
                continue
            mapped = {"WARRIOR_MOMENTUM": "WARRIOR_MOMENTUM_V1", "QUICK_SCALPER": "QUICK_SCALPER"}
            explicit = {mapped[o] for o in owners if o in mapped}
            engine = owner.value if owner else next(iter(explicit)) if len(explicit) == 1 else "UNKNOWN"
            if len(explicit) > 1 or (owner and explicit and explicit != {owner.value}):
                excluded["OWNERSHIP_CONFLICT"] += 1
                continue
            if len(versions) != 1:
                excluded["POLICY_VERSION_CONFLICT"] += 1
                continue
            if len(families) != 1:
                excluded["STRATEGY_FAMILY_CONFLICT"] += 1
                continue
            cash = sum((q*p for _, q, p in sells), Decimal(0)) - sum((q*p for _, q, p in buys), Decimal(0))
            outcome = dict(campaign=campaign, engine=engine, lifecycle=life, instrument=symbol,
                policy_version=next(iter(versions)), opened_at=min(x[0] for x in buys).isoformat(),
                closed_at=max(x[0] for x in sells).isoformat(), quantity=str(bq), net_pnl=str(cash-fees),
                recorded_fees=str(fees), evidence_kind="DURABLE_EQUITY_PAPER_FILLS",
                cost_scope="RECORDED_COMMISSIONS_NO_EXTRA_COST_ASSUMED",
                source_kind="UNVERIFIED_PAPER_LEDGER_PROVENANCE")
            recorded_family = next(iter(families))
            if recorded_family is not None:
                outcome["strategy_family"] = recorded_family
            validate_outcome(outcome)
            outcomes.append(outcome)
    return {"campaign": campaign, "outcomes": outcomes, "excluded": dict(excluded),
            "scope": "CLOSED_ACTIVE_CAMPAIGN_EQUITY_NOT_PAIRED_STRATEGY_COMPARISON"}


def outcome_summary(outcomes):
    from app.asset_modules.outcome_metrics import outcome_metrics
    groups = defaultdict(list)
    for o in outcomes:
        validate_outcome(o)
        groups[(o["campaign"], o["engine"], o["policy_version"], o.get("strategy_family", "UNKNOWN"))].append(o)
    if len(groups) > 100:
        raise ValueError("Outcome group limit exceeded")
    stats = []
    with localcontext() as ctx:
        ctx.prec = 50
        for (campaign, engine, policy, setup), rows in sorted(groups.items()):
            pnls = [amount(o["net_pnl"]) for o in rows]
            stats.append({"campaign": campaign, "engine": engine, "policy_version": policy,
                          "strategy_family": setup,
                          "closed_lifecycles": len(rows), **outcome_metrics(pnls),
                          "cost_scope": rows[0]["cost_scope"], "source_kind": rows[0]["source_kind"]})
    return {"scope": "OBSERVATIONAL_OUTCOMES_NOT_PAIRED_OR_INDEPENDENT_HOLDOUT_EVIDENCE",
            "groups": stats, "promotion": "NONE"}


def main(argv=None):
    from app.asset_modules.grok_learning import LearningJournal
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, default=Path("data/sandbox/paper-execution.sqlite3"))
    parser.add_argument("--journal", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.ledger.resolve() == args.journal.resolve():
        parser.error("Learning journal must be separate from execution ledger")
    report = read_equity_outcomes(args.ledger)
    journal = LearningJournal(args.journal)
    saved = journal.ingest_outcomes(report["outcomes"])
    print(json.dumps({"scope": report["scope"], "campaign": report["campaign"],
                      "import": saved, "excluded": report["excluded"],
                      "feedback": journal.outcome_feedback(), "network_requests": 0, "orders": 0}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
