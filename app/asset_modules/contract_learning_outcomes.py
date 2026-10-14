"""Read-only options/futures simulator outcomes, kept separate from equity fills."""
import argparse
from collections import Counter, defaultdict
from decimal import Decimal, localcontext
from hashlib import sha256
import json
from pathlib import Path

from app.asset_modules.learning_outcomes import amount, instant
from app.trade_intelligence.knowledge.capture_profit_export import read_database, bounded_rows

SPECS = {
    "OPTIONS": (1096044368, "OPTION_QUOTE_PROXY_NOT_EXECUTABLE_FILL", "BUY", "SELL"),
    "FUTURES": (0x41544650, "FUTURES_QUOTE_PROXY_NOT_EXECUTABLE_FILL", "OPEN", "CLOSE"),
}
FIELDS = {"ledger_id", "engine", "intent_id", "instrument", "opened_at", "closed_at",
          "quantity", "net_pnl", "recorded_fees", "policy_version", "strategy_family",
          "evidence_kind", "source_kind", "cost_scope", "reconciliation_scope"}


def validate_proxy_outcome(o):
    if not isinstance(o, dict) or set(o) != FIELDS:
        raise ValueError("Invalid proxy outcome fields")
    for k in FIELDS:
        if not isinstance(o[k], str) or not 1 <= len(o[k]) <= 512:
            raise ValueError("Bounded proxy outcome strings required")
    if o["engine"] not in SPECS or o["evidence_kind"] != SPECS[o["engine"]][1]:
        raise ValueError("Engine-owned quote proxy evidence required")
    expected = ("OPTION_MULTIPLIER_100_CASH_RECONCILIATION" if o["engine"] == "OPTIONS"
                else "EVENT_SETTLEMENT_AGREEMENT_NOT_INDEPENDENT_MULTIPLIER_RECONCILIATION")
    if (o["source_kind"] != "UNVERIFIED_SIMULATOR_INPUT_PROVENANCE"
            or o["cost_scope"] != "RECORDED_SIMULATOR_FEES_AND_FILL_PRICES"
            or o["reconciliation_scope"] != expected
            or o["policy_version"] != "UNKNOWN" or o["strategy_family"] != "UNKNOWN"):
        raise ValueError("Explicit simulation scope and unknown strategy attribution required")
    q = amount(o["quantity"])
    if q <= 0 or q != q.to_integral_value() or amount(o["recorded_fees"]) < 0:
        raise ValueError("Invalid simulator quantity or fees")
    amount(o["net_pnl"])
    if instant(o["closed_at"]) < instant(o["opened_at"]):
        raise ValueError("Simulator exit precedes entry")
    return sha256(json.dumps(["CONTRACT_PROXY_OUTCOME_V1", o["engine"],
                             o["ledger_id"], o["intent_id"]], separators=(",", ":")).encode()).hexdigest()


def read_contract_outcomes(path, engine):
    if engine not in SPECS:
        raise ValueError("OPTIONS or FUTURES required")
    marker, evidence, entry_action, exit_action = SPECS[engine]
    with read_database(path) as db:
        db.execute("BEGIN")
        if db.execute("PRAGMA application_id").fetchone()[0] != marker:
            raise ValueError("Dedicated engine simulator ledger required")
        ledger = db.execute("SELECT settings FROM metadata WHERE id=2").fetchone()
        if ledger is None or not isinstance(ledger[0], str) or not 1 <= len(ledger[0]) <= 512:
            raise ValueError("Durable ledger identity required")
        intents = bounded_rows(db, "SELECT id FROM intents ORDER BY id LIMIT 5001", (), 5000, 2_000_000)
        positions = bounded_rows(db, "SELECT id FROM positions ORDER BY id LIMIT 5001", (), 5000, 2_000_000)
        settlements = bounded_rows(db, "SELECT id,net_pnl,settled_at FROM settlements ORDER BY id LIMIT 5001",
                                   (), 5000, 4_000_000)
        events = bounded_rows(db, "SELECT at,payload FROM journal ORDER BY id LIMIT 10001",
                             (), 10000, 16_000_000)
    intent_ids, open_ids = {r[0] for r in intents}, {r[0] for r in positions}
    closed_ids = {r[0] for r in settlements}
    if not open_ids <= intent_ids or not closed_ids <= intent_ids or open_ids & closed_ids:
        raise ValueError("Simulator ownership/settlement conflict")
    grouped = defaultdict(list)
    for at, raw in events:
        event = json.loads(raw)
        if (not isinstance(event, dict) or event.get("evidence") != evidence
                or event.get("action") not in {entry_action, exit_action}
                or event.get("intent_id") not in intent_ids):
            raise ValueError("Invalid simulator event ownership or evidence")
        grouped[event["intent_id"]].append((instant(at), event))
    # A closed event without settlement is incomplete, not a profitable outcome.
    for identity, rows in grouped.items():
        if any(e["action"] == exit_action for _, e in rows) and identity not in closed_ids:
            raise ValueError("Closing event missing durable settlement")
    excluded = Counter()
    excluded["OPEN_INTENTS"] = len(open_ids)
    excluded["INTENTS_WITHOUT_SETTLEMENT_OR_POSITION"] = len(intent_ids - open_ids - closed_ids)
    outcomes = []
    with localcontext() as ctx:
        ctx.prec = 50
        for identity, net, settled_at in settlements:
            rows = grouped[identity]
            entries = [(t,e) for t,e in rows if e["action"] == entry_action]
            exits = [(t,e) for t,e in rows if e["action"] == exit_action]
            if len(entries) != 1 or len(exits) != 1:
                raise ValueError("Exactly one opening and closing event required")
            opened, entry = entries[0]
            closed, exit_event = exits[0]
            if closed < opened or instant(settled_at) != closed:
                raise ValueError("Simulator settlement timestamp conflict")
            symbol = entry.get("symbol")
            if not isinstance(symbol, str) or not 1 <= len(symbol) <= 512 or exit_event.get("symbol") != symbol:
                raise ValueError("Simulator instrument conflict")
            quantities = [entry.get("quantity"), exit_event.get("quantity")]
            if any(type(q) is not int or q <= 0 for q in quantities) or quantities[0] != quantities[1]:
                raise ValueError("Simulator quantity conflict")
            price_in, price_out = amount(entry["fill"]), amount(exit_event["fill"])
            fee_in, fee_out = amount(entry["fees"]), amount(exit_event["fees"])
            pnl = amount(net)
            if (price_in <= 0 or price_out < 0 or fee_in < 0 or fee_out < 0
                    or amount(exit_event["net_pnl"]) != pnl):
                raise ValueError("Simulator amount/settlement conflict")
            if engine == "OPTIONS":
                if (price_out-price_in)*100*quantities[0]-fee_in-fee_out != pnl:
                    raise ValueError("Option cash PnL conflict")
                reconciliation = "OPTION_MULTIPLIER_100_CASH_RECONCILIATION"
            else:
                if (entry.get("side") not in {"LONG", "SHORT"}
                        or exit_event.get("side") != entry["side"] or price_out <= 0):
                    raise ValueError("Futures side/price conflict")
                # Legacy events do not retain contract multipliers. Never infer one.
                reconciliation = "EVENT_SETTLEMENT_AGREEMENT_NOT_INDEPENDENT_MULTIPLIER_RECONCILIATION"
            o = dict(ledger_id=ledger[0], engine=engine, intent_id=identity, instrument=symbol,
                     opened_at=opened.isoformat(), closed_at=closed.isoformat(),
                     quantity=str(quantities[0]), net_pnl=str(pnl), recorded_fees=str(fee_in+fee_out),
                     policy_version="UNKNOWN", strategy_family="UNKNOWN", evidence_kind=evidence,
                     source_kind="UNVERIFIED_SIMULATOR_INPUT_PROVENANCE",
                     cost_scope="RECORDED_SIMULATOR_FEES_AND_FILL_PRICES", reconciliation_scope=reconciliation)
            validate_proxy_outcome(o)
            outcomes.append(o)
    return {"scope": "CLOSED_SIMULATOR_OUTCOMES_NOT_BROKER_FILLS_OR_STRATEGY_VALIDATION",
            "engine": engine, "ledger_id": ledger[0], "outcomes": outcomes,
            "excluded": {k:v for k,v in excluded.items() if v}}


def proxy_summary(outcomes):
    groups = defaultdict(list)
    for o in outcomes:
        validate_proxy_outcome(o)
        groups[(o["engine"], o["ledger_id"], o["evidence_kind"], o["reconciliation_scope"])].append(o)
    if len(groups) > 100:
        raise ValueError("Proxy feedback group limit exceeded")
    stats = []
    with localcontext() as ctx:
        ctx.prec = 50
        for (engine, ledger, evidence, reconciliation), rows in sorted(groups.items()):
            pnls = [amount(o["net_pnl"]) for o in rows]
            stats.append(dict(engine=engine, ledger_id=ledger, evidence_kind=evidence,
                              reconciliation_scope=reconciliation, policy_version="UNKNOWN",
                              strategy_family="UNKNOWN", closed_intents=len(rows),
                              net_pnl=str(sum(pnls, Decimal(0))), wins=sum(p>0 for p in pnls),
                              losses=sum(p<0 for p in pnls), breakeven=sum(p==0 for p in pnls),
                              source_kind=rows[0]["source_kind"], cost_scope=rows[0]["cost_scope"]))
    return {"scope": "QUOTE_SIMULATION_FEEDBACK_NOT_EQUITY_FILLS_OR_INDEPENDENT_HOLDOUT",
            "groups": stats, "promotion": "NONE"}


def main(argv=None):
    from app.asset_modules.grok_learning import LearningJournal
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=sorted(SPECS), required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--journal", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.ledger.resolve() == args.journal.resolve():
        parser.error("Learning journal must be separate from simulator ledger")
    report = read_contract_outcomes(args.ledger, args.engine)
    journal = LearningJournal(args.journal)
    saved = journal.ingest_proxy_outcomes(report["outcomes"])
    print(json.dumps({**{k:v for k,v in report.items() if k != "outcomes"},
                      "import": saved, "feedback": journal.proxy_outcome_feedback(),
                      "network_requests": 0, "orders": 0, "promotion": "NONE"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
