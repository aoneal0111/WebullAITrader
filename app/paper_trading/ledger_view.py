"""Bounded read-only equity PAPER ledger snapshot; no runtime construction."""
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, localcontext
import json
from pathlib import Path
import re
import sqlite3
import time

from app.asset_modules.engine_catalog import EngineId, lifecycle_owner
from app.asset_modules.learning_outcomes import amount, instant
from app.read_models.orders import OrderReadModel, OrdersReadModelSnapshot

TERMINAL = {"FILLED", "CANCELLED", "REJECTED", "EXPIRED"}


def _text(value, limit=512):
    if not isinstance(value, str) or not 0 < len(value) <= limit or not value.isprintable() or value != value.strip():
        raise ValueError("Invalid saved text")
    return value


def _optional(value):
    return None if value is None or value == "" else _text(value)


def _price(value):
    if value is not None and amount(value) <= 0:
        raise ValueError("Invalid saved price")
    return value


@dataclass(frozen=True)
class PaperLedgerView:
    status: str
    read_at: str
    notice: str
    campaign: str | None = None
    orders: OrdersReadModelSnapshot = OrdersReadModelSnapshot()

    def report(self):
        return dict(scope="READ_ONLY_SAVED_PAPER_ORDERS_NOT_RUNTIME_HEALTH",
                    status=self.status, read_at=self.read_at, notice=self.notice, campaign=self.campaign,
                    saved_orders=len(self.orders.orders) if self.status == "AVAILABLE" else None,
                    saved_working_orders=(sum(o.status not in TERMINAL for o in self.orders.orders)
                                          if self.status == "AVAILABLE" else None),
                    runtime_health="UNKNOWN", feed_health="UNKNOWN", orders_submitted=0, network_requests=0)


class PaperLedgerReader:
    def __init__(self, path):
        self.path = Path(path).resolve()

    def read(self):
        at = datetime.now(timezone.utc).isoformat()
        def failure(status, notice):
            return PaperLedgerView(status, at, notice)
        if not self.path.is_file():
            return failure("NOT_FOUND", "Saved PAPER ledger not found; inventory is unknown.")
        deadline = time.monotonic() + .75
        try:
            with closing(sqlite3.connect(self.path.as_uri()+"?mode=ro", uri=True, timeout=.05)) as db:
                db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
                db.execute("PRAGMA query_only=ON")
                db.execute("BEGIN")
                metadata = db.execute("SELECT key,CASE WHEN length(value)<=4096 THEN value ELSE NULL END FROM metadata LIMIT 101").fetchall()
                if len(metadata) > 100:
                    raise ValueError("Metadata limit")
                meta = dict(metadata)
                if (meta.get("environment") != "PAPER" or meta.get("account_id") != "paper-account"
                        or meta.get("schema_version") != "1"):
                    raise ValueError("Dedicated PAPER ledger required")
                campaign = _text(meta["active_campaign_id"], 180)
                if campaign == "no-active-paper-campaign":
                    raise ValueError("No active campaign")
                cursor = db.execute("""SELECT order_id, CASE WHEN length(payload)<=131072 THEN payload ELSE NULL END
                    FROM orders WHERE COALESCE(json_extract(payload,'$.paper_campaign_id'),'legacy-paper-campaign')=?
                    ORDER BY order_id LIMIT 5001""", (campaign,))
                orders, fill_ids, size = [], set(), 0
                with localcontext() as ctx:
                    ctx.prec = 50
                    for identity, raw in cursor:
                        if not isinstance(raw, str) or len(orders) >= 5000 or time.monotonic() > deadline:
                            raise ValueError("Saved result limit")
                        size += len(raw.encode("utf-8"))
                        if size > 32_000_000:
                            raise ValueError("Saved byte limit")
                        value = json.loads(raw)
                        r = value["request"]
                        metadata = r.get("metadata") or {}
                        if not isinstance(metadata, dict):
                            raise ValueError("Invalid saved metadata")
                        if value["order_id"] != identity or r["asset_class"] != "STOCK":
                            raise ValueError("Saved order identity or asset disagreement")
                        side, status, kind = r["side"], value["status"], r["order_type"]
                        if (side not in {"BUY","SELL"} or status not in TERMINAL | {"NEW","ACCEPTED","PARTIALLY_FILLED"}
                                or kind not in {"MARKET","LIMIT","STOP","STOP_LIMIT"}):
                            raise ValueError("Unsupported saved order")
                        qty, filled = amount(r["quantity"]), amount(value["filled_quantity"])
                        if qty <= 0 or not 0 <= filled <= qty or status == "FILLED" and filled != qty:
                            raise ValueError("Saved quantity disagreement")
                        created, updated = instant(value["created_at"]), instant(value["updated_at"])
                        if updated < created:
                            raise ValueError("Saved order time disagreement")
                        total = Decimal(0)
                        for fill in value["fills"]:
                            fid = _text(fill["fill_id"])
                            q, p, fee = amount(fill["quantity"]), amount(fill["price"]), amount(fill["commission"])
                            if (fid in fill_ids or fill["order_id"] != identity or q <= 0 or p <= 0 or fee < 0
                                    or not created <= instant(fill["timestamp"]) <= updated):
                                raise ValueError("Invalid saved fill")
                            fill_ids.add(fid)
                            total += q
                        if total != filled:
                            raise ValueError("Saved fills disagree")
                        symbol = _text(r["symbol"], 16)
                        if re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,15}", symbol) is None:
                            raise ValueError("Invalid saved symbol")
                        life = _optional(r.get("strategy_lifecycle_id"))
                        owner = lifecycle_owner(life)
                        declared = metadata.get("strategy_owner")
                        if owner in {EngineId.WARRIOR, EngineId.SCALPER}:
                            permitted = ({"WARRIOR_MOMENTUM", "WARRIOR_MOMENTUM_V1"}
                                         if owner is EngineId.WARRIOR else {"QUICK_SCALPER"})
                            if declared not in permitted | {None, ""}:
                                raise ValueError("Saved strategy ownership conflict")
                        limit, stop, avg = (_price(r.get("limit_price")), _price(r.get("stop_price")),
                                            _price(value.get("average_fill_price")))
                        if kind in {"LIMIT","STOP_LIMIT"} and limit is None or kind in {"STOP","STOP_LIMIT"} and stop is None:
                            raise ValueError("Saved order price missing")
                        orders.append(OrderReadModel(
                            order_id=_text(identity), symbol=symbol, side=side, quantity=str(qty), status=status,
                            updated_at=updated, order_type=kind, limit_price=limit, stop_price=stop,
                            filled_quantity=str(filled), remaining_quantity=str(qty-filled), average_fill_price=avg,
                            submitted_at=created, lifecycle_id=life, execution_reason=_optional(r.get("execution_reason"))))
            return PaperLedgerView("AVAILABLE", at,
                "Saved current-campaign orders and net fills only. Broker positions, runtime/feed health and protection effectiveness are unverified.",
                campaign, OrdersReadModelSnapshot(tuple(orders)))
        except sqlite3.OperationalError as exc:
            if getattr(exc, "sqlite_errorcode", 0) & 255 in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
                return failure("BUSY", "Saved ledger is busy; inventory is unknown. Refresh to retry.")
            return failure("UNAVAILABLE", "Saved ledger could not be read within limits; inventory is unknown.")
        except (sqlite3.DatabaseError, OSError, ValueError, TypeError, KeyError, ArithmeticError, RecursionError):
            return failure("INVALID", "Saved ledger failed validation; inventory is unknown.")
