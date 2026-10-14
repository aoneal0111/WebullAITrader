"""Read-only linked crypto simulator outcomes; no broker or model calls."""
import argparse
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext
from hashlib import sha256
import json
from pathlib import Path
import re

from app.asset_modules.learning_outcomes import instant
from app.trade_intelligence.knowledge.capture_profit_export import read_database, bounded_rows

EVIDENCE = "CRYPTO_SNAPSHOT_SIMULATION_NOT_EXECUTABLE_FILL"
RECONCILIATION = "LINKED_DISPATCH_PNL_AGREEMENT_NOT_INDEPENDENT_FEE_RECONCILIATION"
COST_SCOPE = "RECORDED_DISPATCH_NET_PNL_FEES_NOT_SEPARATELY_RECORDED"


def crypto_amount(value):
    # Native crypto cost and PnL records retain more than 12 fractional digits.
    if not isinstance(value, str) or not 1 <= len(value) <= 128:
        raise ValueError("Bounded crypto Decimal string required")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("Invalid crypto amount") from exc
    if (not result.is_finite() or len(result.as_tuple().digits) > 100
            or not -100 <= result.as_tuple().exponent <= 12
            or result.copy_abs() > Decimal('1e12')):
        raise ValueError("Crypto amount outside bounds")
    return result


def validate_crypto_outcome(o):
    from app.asset_modules.contract_learning_outcomes import FIELDS
    if not isinstance(o, dict) or set(o) != FIELDS:
        raise ValueError("Invalid crypto outcome fields")
    if any(not isinstance(v, str) or not 1 <= len(v) <= 512 for v in o.values()):
        raise ValueError("Bounded crypto outcome labels required")
    if (o["engine"] != "CRYPTO" or o["evidence_kind"] != EVIDENCE
            or o["reconciliation_scope"] != RECONCILIATION
            or o["cost_scope"] != COST_SCOPE or o["recorded_fees"] != "UNKNOWN"
            or o["source_kind"] != "UNVERIFIED_SIMULATOR_INPUT_PROVENANCE"
            or o["policy_version"] != "UNKNOWN" or o["strategy_family"] != "UNKNOWN"):
        raise ValueError("Explicit crypto simulation scope required")
    if not re.fullmatch(r"[A-Z0-9]{1,30}/USD", o["instrument"]):
        raise ValueError("Canonical USD crypto instrument required")
    if crypto_amount(o["quantity"]) <= 0:
        raise ValueError("Positive crypto quantity required")
    crypto_amount(o["net_pnl"])
    if instant(o["closed_at"]) < instant(o["opened_at"]):
        raise ValueError("Crypto exit precedes entry")
    return sha256(json.dumps(["CRYPTO_PROXY_OUTCOME_V1", o["ledger_id"], o["intent_id"]],
                             separators=(",", ":")).encode()).hexdigest()


def _pnl_agrees(parts, total):
    # Accept exact accumulation or the simulator's default 28-digit sequential
    # accumulation. No money tolerance or inferred fee rate is used.
    sums = []
    for precision in (150, 28):
        with localcontext() as ctx:
            ctx.prec, ctx.rounding = precision, ROUND_HALF_EVEN
            value = Decimal(0)
            for part in parts:
                value += part
            sums.append(value)
    return total in sums


def read_crypto_outcomes(path):
    with read_database(path) as db:
        db.execute("BEGIN")
        if db.execute("PRAGMA application_id").fetchone()[0] != 0x41544350:
            raise ValueError("Dedicated Crypto PAPER ledger required")
        ledger = db.execute("SELECT value FROM crypto_metadata WHERE key='ledger_id'").fetchone()
        if ledger is None or not isinstance(ledger[0], str) or not 1 <= len(ledger[0]) <= 512:
            raise ValueError("Durable crypto ledger identity required")
        intents = bounded_rows(db, "SELECT id,fingerprint,symbol,quantity,entry_cost,realized_pnl,opened_at,closed_at FROM crypto_intents ORDER BY id LIMIT 5001",
                               (), 5000, 8_000_000)
        dispatches = bounded_rows(db, "SELECT id,fingerprint,entry_id,quantity,net_pnl,at FROM crypto_dispatches ORDER BY rowid LIMIT 20001",
                                  (), 20000, 8_000_000)
        events = bounded_rows(db, "SELECT id,at,payload FROM journal ORDER BY rowid LIMIT 20001",
                              (), 20000, 16_000_000)
        links = dict(bounded_rows(db, "SELECT intent_id,symbol FROM crypto_position_intents LIMIT 5001",
                                 (), 5000, 2_000_000))
        positions = dict(bounded_rows(db, "SELECT symbol,quantity FROM positions LIMIT 5001",
                                     (), 5000, 2_000_000))
    ids = {r[0] for r in intents}
    if not set(links) <= ids or any(s not in positions for s in links.values()):
        raise ValueError("Crypto position ownership conflict")
    by_event = {i: (instant(at), json.loads(raw)) for i,at,raw in events}
    groups, excluded = defaultdict(list), Counter()
    excluded["LEGACY_UNLINKED_JOURNAL_EVENTS"] = len(set(by_event)-{r[0] for r in dispatches})
    excluded["LEGACY_UNLINKED_POSITIONS"] = len(set(positions)-set(links.values()))
    for identity, fingerprint, entry_id, quantity, pnl, at in dispatches:
        if entry_id is None:
            excluded["LEGACY_UNLINKED_DISPATCHES"] += 1
            continue
        if entry_id not in ids or identity not in by_event:
            raise ValueError("Crypto dispatch lacks owned entry or journal event")
        event_at, event = by_event[identity]
        q = crypto_amount(quantity)
        if (not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint)
                or not isinstance(event, dict) or event.get("model") != "SNAPSHOT_SIMULATION"
                or event.get("action") not in {"BUY", "SELL"}
                or event_at != instant(at) or crypto_amount(event["quantity"]) != q
                or q <= 0 or crypto_amount(event["fill"]) <= 0):
            raise ValueError("Crypto dispatch/journal conflict")
        if (event["action"] == "BUY") != (pnl is None):
            raise ValueError("Crypto action/PnL conflict")
        groups[entry_id].append((identity, fingerprint, q, None if pnl is None else crypto_amount(pnl), event_at, event))
    outcomes = []
    with localcontext() as ctx:
        ctx.prec, ctx.rounding = 150, ROUND_HALF_EVEN
        for identity, fingerprint, symbol, bought, cost, total, opened, closed in intents:
            quantity, entry_cost, net = map(crypto_amount, (bought, cost, total))
            opened_at = instant(opened)
            if quantity <= 0 or entry_cost <= 0:
                raise ValueError("Invalid crypto entry amounts")
            rows = groups[identity]
            buys = [r for r in rows if r[3] is None]
            sells = [r for r in rows if r[3] is not None]
            if (len(buys) != 1 or rows[0] != buys[0] or buys[0][:3] != (identity, fingerprint, quantity)
                    or buys[0][4] != opened_at or any(r[5].get("symbol") != symbol for r in rows)):
                raise ValueError("Crypto opening dispatch conflict")
            if any(b[4] < a[4] for a,b in zip(rows, rows[1:])):
                raise ValueError("Crypto dispatch chronology conflict")
            sold = sum((r[2] for r in sells), Decimal(0))
            running = Decimal(0)
            for r in sells[:-1]:
                running += r[2]
                if running >= quantity:
                    raise ValueError("Crypto inventory closed before final dispatch")
            if not _pnl_agrees([r[3] for r in sells], net):
                raise ValueError("Crypto linked net PnL conflict")
            if closed is None:
                if (links.get(identity) != symbol or sold >= quantity
                        or crypto_amount(positions[symbol]) != quantity-sold):
                    raise ValueError("Open crypto inventory conflict")
                excluded["OPEN_INTENTS"] += 1
                continue
            closed_at = instant(closed)
            if (identity in links or sold != quantity or not sells or closed_at != sells[-1][4]
                    or closed_at < opened_at):
                raise ValueError("Closed crypto inventory/timestamp conflict")
            o = dict(ledger_id=ledger[0], engine="CRYPTO", intent_id=identity, instrument=symbol,
                     opened_at=opened_at.isoformat(), closed_at=closed_at.isoformat(),
                     quantity=str(quantity), net_pnl=str(net), recorded_fees="UNKNOWN",
                     policy_version="UNKNOWN", strategy_family="UNKNOWN", evidence_kind=EVIDENCE,
                     source_kind="UNVERIFIED_SIMULATOR_INPUT_PROVENANCE", cost_scope=COST_SCOPE,
                     reconciliation_scope=RECONCILIATION)
            validate_crypto_outcome(o)
            outcomes.append(o)
    return {"scope": "CLOSED_LINKED_CRYPTO_SIMULATION_NOT_BROKER_FILLS_OR_STRATEGY_VALIDATION",
            "engine": "CRYPTO", "ledger_id": ledger[0], "outcomes": outcomes,
            "excluded": {k:v for k,v in excluded.items() if v}}


def main(argv=None):
    from app.asset_modules.grok_learning import LearningJournal
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--journal", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.ledger.resolve() == args.journal.resolve():
        parser.error("Learning journal must be separate from crypto ledger")
    report = read_crypto_outcomes(args.ledger)
    journal = LearningJournal(args.journal)
    saved = journal.ingest_proxy_outcomes(report["outcomes"])
    print(json.dumps({**{k:v for k,v in report.items() if k != "outcomes"},
                      "import": saved, "feedback": journal.proxy_outcome_feedback(),
                      "network_requests": 0, "orders": 0, "promotion": "NONE"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
