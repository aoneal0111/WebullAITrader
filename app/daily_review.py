"""Sanitized, bounded daily PAPER review bundles for operator analysis."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
import json
from pathlib import Path
from typing import Any

from app.market.calendar import EASTERN


class DailyReviewExporter:
    """Write one local JSON review bundle per UTC trading day.

    The bundle contains decision-support facts only. It deliberately excludes
    credentials, broker account identifiers, request headers, and raw provider
    responses. Export failure never blocks desktop shutdown.
    """

    def __init__(
        self,
        root: str | Path,
        *,
        retention_days: int = 14,
        clock=lambda: datetime.now(UTC),
        catalyst_watch_path: str | Path | None = None,
    ) -> None:
        if isinstance(retention_days, bool) or retention_days <= 0:
            raise ValueError("retention_days must be positive")
        self.root = Path(root)
        self.retention_days = retention_days
        self._clock = clock
        self._catalyst_watch_path = (
            None if catalyst_watch_path is None else Path(catalyst_watch_path)
        )

    def export(self, composition: object) -> Path | None:
        try:
            now = _aware_utc(self._clock())
            self.root.mkdir(parents=True, exist_ok=True)
            payload = self._payload(composition, now)
            target = self.root / f"atlas-daily-review-{now.date().isoformat()}.json"
            temporary = target.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps(payload, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            temporary.replace(target)
            self.prune(now.date())
            return target
        except Exception:
            return None

    def prune(self, today: date | None = None) -> tuple[Path, ...]:
        current = today or _aware_utc(self._clock()).date()
        cutoff = current - timedelta(days=self.retention_days - 1)
        removed: list[Path] = []
        if not self.root.exists():
            return ()
        for path in self.root.glob("atlas-daily-review-*.json"):
            parsed = _date_from_name(path.name)
            if parsed is not None and parsed < cutoff:
                try:
                    path.unlink()
                    removed.append(path)
                except OSError:
                    pass
        return tuple(sorted(removed))

    def _payload(self, composition: object, generated_at: datetime) -> dict[str, Any]:
        trading_date = generated_at.astimezone(EASTERN).date()
        history = _authoritative_orders(composition)
        daily_history = tuple(
            order for order in history
            if _order_touches_trading_date(order, trading_date)
        )
        orders = [_order(order) for order in daily_history]
        fills = [
            fill
            for order in orders
            for fill in order["fills"]
        ]
        projections = getattr(composition, "runtime_projections", None)
        positions = _snapshot_items(
            getattr(getattr(projections, "position_projection", None), "snapshot", None),
            "positions",
        )
        account = _json_value(
            getattr(
                getattr(getattr(projections, "paper_account_projection", None), "snapshot", None),
                "__dict__",
                getattr(getattr(projections, "paper_account_projection", None), "snapshot", None),
            )
        )
        seeds = _load_catalyst_seeds(self._catalyst_watch_path)
        attribution = _lifecycle_attribution(fills)
        experiment_status = _experiment_status(composition)
        return {
            "schema_version": 2,
            "generated_at": generated_at.isoformat(),
            "trading_date": trading_date.isoformat(),
            "trading_timezone": str(EASTERN),
            "environment": "PAPER",
            "summary": {
                "order_count": len(orders),
                "fill_count": len(fills),
                "filled_buy_count": sum(
                    fill["side"] == "BUY" for fill in fills
                ),
                "filled_sell_count": sum(
                    fill["side"] == "SELL" for fill in fills
                ),
                "active_position_count": len(
                    [item for item in positions if _nonzero(item.get("quantity"))]
                ),
                "catalyst_watch_seed_count": len(seeds),
                "attributed_lifecycle_count": len(attribution),
                "attributed_net_realized_pnl": _json_value(sum(
                    (
                        Decimal(item["net_realized_pnl"])
                        for item in attribution
                    ),
                    Decimal("0"),
                )),
            },
            "account": account,
            "positions": positions,
            "orders": orders,
            "fills": fills,
            "performance_attribution": attribution,
            "shadow_experiments": experiment_status,
            "catalyst_watch_seeds": seeds,
            "privacy": {
                "credentials_included": False,
                "broker_account_identifiers_included": False,
                "raw_provider_payloads_included": False,
            },
        }


def _experiment_status(composition: object) -> dict[str, Any] | None:
    policy = getattr(composition, "paper_entry_intelligence", None)
    journal = getattr(policy, "_journal", None)
    status = getattr(journal, "status", None)
    if not callable(status):
        return None
    try:
        value = status()
    except Exception:
        return None
    return _json_value(value) if isinstance(value, dict) else None


def _authoritative_orders(composition: object) -> tuple[object, ...]:
    commands = getattr(composition, "paper_trading_commands", None)
    durable_store = getattr(commands, "durable_store", None)
    if durable_store is not None:
        try:
            return tuple(durable_store.orders())
        except Exception:
            # Review export is best-effort; retain the in-memory fallback.
            pass
    order_book = getattr(composition, "paper_order_book", None)
    return tuple(order_book.history()) if order_book is not None else ()


def _order_touches_trading_date(order: object, trading_date: date) -> bool:
    timestamps = [
        getattr(order, "created_at", None),
        getattr(order, "updated_at", None),
    ]
    timestamps.extend(
        getattr(fill, "timestamp", None)
        for fill in getattr(order, "fills", ())
    )
    for timestamp in timestamps:
        if not isinstance(timestamp, datetime):
            continue
        try:
            if _aware_utc(timestamp).astimezone(EASTERN).date() == trading_date:
                return True
        except ValueError:
            continue
    return False


def _order(order: object) -> dict[str, Any]:
    request = getattr(order, "request", None)
    side = _enum(getattr(request, "side", None))
    symbol = str(getattr(order, "symbol", "")).strip().upper()
    lifecycle = getattr(request, "strategy_lifecycle_id", None)
    values = {
        "order_id": str(getattr(order, "order_id", "")),
        "symbol": symbol,
        "side": side,
        "order_type": _enum(getattr(request, "order_type", None)),
        "status": _enum(getattr(order, "status", None)),
        "quantity": _json_value(getattr(request, "quantity", None)),
        "filled_quantity": _json_value(getattr(order, "filled_quantity", None)),
        "remaining_quantity": _json_value(getattr(order, "remaining_quantity", None)),
        "average_fill_price": _json_value(getattr(order, "average_fill_price", None)),
        "limit_price": _json_value(getattr(request, "limit_price", None)),
        "stop_price": _json_value(getattr(request, "stop_price", None)),
        "execution_reason": getattr(request, "execution_reason", None),
        "strategy_lifecycle_id": lifecycle,
        "created_at": _json_value(getattr(order, "created_at", None)),
        "updated_at": _json_value(getattr(order, "updated_at", None)),
        "fills": [],
    }
    values["fills"] = [
        {
            "order_id": values["order_id"],
            "symbol": symbol,
            "side": side,
            "quantity": _json_value(getattr(fill, "quantity", None)),
            "price": _json_value(
                getattr(fill, "price", getattr(fill, "fill_price", None))
            ),
            "commission": _json_value(getattr(fill, "commission", None)),
            "timestamp": _json_value(getattr(fill, "timestamp", None)),
            "execution_reason": values["execution_reason"],
            "strategy_lifecycle_id": lifecycle,
        }
        for fill in getattr(order, "fills", ())
    ]
    return values


def _lifecycle_attribution(
    fills: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for fill in fills:
        lifecycle = str(
            fill.get("strategy_lifecycle_id")
            or f"UNATTRIBUTED:{fill.get('order_id', '')}"
        )
        symbol = str(fill.get("symbol") or "").strip().upper()
        grouped.setdefault((lifecycle, symbol), []).append(fill)

    results: list[dict[str, Any]] = []
    for (lifecycle, symbol), lifecycle_fills in sorted(grouped.items()):
        buys = [fill for fill in lifecycle_fills if fill.get("side") == "BUY"]
        sells = [fill for fill in lifecycle_fills if fill.get("side") == "SELL"]
        buy_quantity = sum((_decimal(fill.get("quantity")) for fill in buys), Decimal("0"))
        sell_quantity = sum((_decimal(fill.get("quantity")) for fill in sells), Decimal("0"))
        buy_notional = sum((
            _decimal(fill.get("quantity")) * _decimal(fill.get("price"))
            for fill in buys
        ), Decimal("0"))
        sell_notional = sum((
            _decimal(fill.get("quantity")) * _decimal(fill.get("price"))
            for fill in sells
        ), Decimal("0"))
        average_entry = (
            None if buy_quantity == 0 else buy_notional / buy_quantity
        )
        average_exit = (
            None if sell_quantity == 0 else sell_notional / sell_quantity
        )
        matched_quantity = min(buy_quantity, sell_quantity)
        gross_realized = (
            Decimal("0")
            if average_entry is None or average_exit is None
            else matched_quantity * (average_exit - average_entry)
        )
        commissions = sum((
            _decimal(fill.get("commission")) for fill in lifecycle_fills
        ), Decimal("0"))
        first_entry = _first_timestamp(buys)
        last_exit = _last_timestamp(sells)
        holding_seconds = (
            None
            if first_entry is None or last_exit is None
            else max(0, int((last_exit - first_entry).total_seconds()))
        )
        remaining = buy_quantity - sell_quantity
        status = (
            "CLOSED" if buy_quantity > 0 and remaining == 0
            else "OPEN" if buy_quantity > 0 and remaining > 0
            else "OVER_EXITED" if remaining < 0
            else "EXIT_ONLY" if sell_quantity > 0
            else "NO_FILLS"
        )
        results.append({
            "strategy_lifecycle_id": lifecycle,
            "symbol": symbol,
            "status": status,
            "entry_quantity": _json_value(buy_quantity),
            "exit_quantity": _json_value(sell_quantity),
            "remaining_quantity": _json_value(remaining),
            "matched_quantity": _json_value(matched_quantity),
            "average_entry_price": _json_value(average_entry),
            "average_exit_price": _json_value(average_exit),
            "gross_realized_pnl": _json_value(gross_realized),
            "commissions": _json_value(commissions),
            "net_realized_pnl": _json_value(gross_realized - commissions),
            "holding_seconds": holding_seconds,
            "first_entry_at": _json_value(first_entry),
            "last_exit_at": _json_value(last_exit),
            "exit_reasons": sorted({
                str(fill["execution_reason"])
                for fill in sells
                if fill.get("execution_reason")
            }),
        })
    return results


def _decimal(value: object) -> Decimal:
    if value is None:
        return Decimal("0")
    try:
        result = Decimal(str(value))
    except Exception:
        return Decimal("0")
    return result if result.is_finite() else Decimal("0")


def _fill_timestamp(fill: dict[str, Any]) -> datetime | None:
    value = fill.get("timestamp")
    if isinstance(value, datetime):
        timestamp = value
    elif isinstance(value, str):
        try:
            timestamp = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    try:
        return _aware_utc(timestamp)
    except ValueError:
        return None


def _first_timestamp(fills: list[dict[str, Any]]) -> datetime | None:
    values = [value for fill in fills if (value := _fill_timestamp(fill)) is not None]
    return min(values) if values else None


def _last_timestamp(fills: list[dict[str, Any]]) -> datetime | None:
    values = [value for fill in fills if (value := _fill_timestamp(fill)) is not None]
    return max(values) if values else None


def _snapshot_items(snapshot: object, attribute: str) -> list[dict[str, Any]]:
    return [
        _json_value(item)
        for item in getattr(snapshot, attribute, ())
    ]


def _load_catalyst_seeds(path: Path | None) -> list[dict[str, Any]]:
    if path is None or not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(payload, list):
        return []
    return [
        {
            key: value
            for key, value in item.items()
            if key in {
                "symbol", "headline", "source", "source_url",
                "provider_event_id", "published_at", "discovered_at",
            }
        }
        for item in payload
        if isinstance(item, dict)
    ]


def _json_value(value: object) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return _aware_utc(value).isoformat()
    if is_dataclass(value):
        return {
            key: _json_value(item)
            for key, item in asdict(value).items()
            if key not in {"account_id", "account_id_redacted"}
        }
    if isinstance(value, dict):
        return {
            str(key): _json_value(item)
            for key, item in value.items()
            if str(key) not in {"account_id", "account_id_redacted"}
        }
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    attributes = getattr(value, "__dict__", None)
    if isinstance(attributes, dict):
        return {
            str(key): _json_value(item)
            for key, item in attributes.items()
            if str(key) not in {"account_id", "account_id_redacted"}
        }
    enum_value = getattr(value, "value", None)
    if isinstance(enum_value, (str, int, float, bool)):
        return enum_value
    slots = getattr(type(value), "__slots__", ())
    if slots:
        return {
            name: _json_value(getattr(value, name))
            for name in slots
            if hasattr(value, name) and name not in {"account_id", "account_id_redacted"}
        }
    return str(value)


def _enum(value: object) -> str | None:
    if value is None:
        return None
    return str(getattr(value, "value", value)).strip().upper() or None


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("review clock must be timezone-aware")
    return value.astimezone(UTC)


def _date_from_name(name: str) -> date | None:
    prefix = "atlas-daily-review-"
    suffix = ".json"
    if not name.startswith(prefix) or not name.endswith(suffix):
        return None
    try:
        return date.fromisoformat(name[len(prefix):-len(suffix)])
    except ValueError:
        return None


def _nonzero(value: object) -> bool:
    try:
        return Decimal(str(value)) != Decimal("0")
    except Exception:
        return False


__all__ = ["DailyReviewExporter"]
