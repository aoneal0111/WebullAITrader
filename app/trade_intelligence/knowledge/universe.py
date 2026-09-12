"""Read-only Alpaca asset-master snapshots for historical research universes."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import httpx

ALPACA_ASSET_URL = "https://paper-api.alpaca.markets"
UNIVERSE_PROVENANCE = "ALPACA_ASSET_MASTER_CURRENT_SNAPSHOT"
UNIVERSE_LIMITATION = "NOT_POINT_IN_TIME_UNIVERSE"
UNIVERSE_FILTER_VERSION = "ATLAS_RESEARCH_UNIVERSE_FILTER_V1"
ALLOWED_EXCHANGES = frozenset(("NASDAQ", "NYSE", "AMEX", "ARCA", "NYSEARCA", "BATS"))


def _atomic_write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    temporary.replace(path)


class ResearchSymbolUniverseProvider:
    def symbols(self) -> tuple[str, ...]:
        raise NotImplementedError


class AlpacaAssetMasterClient(ResearchSymbolUniverseProvider):
    """Asset reference client with no trading/action methods or fallback URL."""

    def __init__(self, api_key: str, api_secret: str, *, transport: httpx.BaseTransport | None = None,
                 snapshot_root: Path = Path("data/research/universes/alpaca")) -> None:
        if not api_key.strip() or not api_secret.strip():
            raise ValueError("Alpaca credentials are required")
        self.snapshot_root = Path(snapshot_root)
        self._client = httpx.Client(base_url=ALPACA_ASSET_URL,
                                    headers={"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": api_secret},
                                    transport=transport, timeout=20.0)
        self.request_count = 0

    @classmethod
    def from_environment(cls, **kwargs: Any) -> "AlpacaAssetMasterClient":
        return cls(os.environ.get("ALPACA_API_KEY", ""), os.environ.get("ALPACA_API_SECRET", ""), **kwargs)

    def close(self) -> None:
        self._client.close()

    def fetch_assets(self, *, status: str = "active", asset_class: str = "us_equity") -> tuple[dict[str, Any], ...]:
        if status not in ("active", "inactive") or asset_class != "us_equity":
            raise ValueError("only US equity asset-master reads are permitted")
        response = self._client.get("/v2/assets", params={"status": status, "asset_class": asset_class})
        self.request_count += 1
        if response.status_code >= 400:
            raise RuntimeError(f"ALPACA_ASSET_MASTER_{response.status_code}")
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError("MALFORMED_ASSET_MASTER_RESPONSE")
        return tuple(item for item in payload if isinstance(item, dict))

    def snapshot(self, *, include_inactive: bool = True, exclude_otc: bool = True) -> dict[str, Any]:
        assets = list(self.fetch_assets(status="active"))
        if include_inactive:
            assets.extend(self.fetch_assets(status="inactive"))
        raw = json.dumps(assets, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(raw.encode()).hexdigest()
        normalized, excluded = normalize_assets(assets, exclude_otc=exclude_otc)
        timestamp = datetime.now(UTC).isoformat()
        result = {"schema_version": 1, "provider": "ALPACA", "provenance": UNIVERSE_PROVENANCE,
                  "limitation": UNIVERSE_LIMITATION, "universe_as_of_timestamp": timestamp,
                  "filter_version": UNIVERSE_FILTER_VERSION, "content_hash": digest,
                  "raw_asset_count": len(assets), "raw_assets": assets, "assets": normalized, "excluded": excluded,
                  "request_count": self.request_count}
        _atomic_write(self.snapshot_root / timestamp.replace(":", "").replace("+", "_") / "universe.json", result)
        return result

    def symbols(self) -> tuple[str, ...]:
        latest = sorted(self.snapshot_root.glob("*/universe.json"), key=lambda path: path.stat().st_mtime, reverse=True)
        if latest:
            payload = json.loads(latest[0].read_text(encoding="utf-8"))
            return tuple(item["symbol"] for item in payload.get("assets", ()) if item.get("included"))
        return tuple(item["symbol"] for item in self.snapshot()["assets"] if item.get("included"))


def normalize_assets(rows: list[dict[str, Any]], *, exclude_otc: bool = True) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    seen: set[str] = set(); included: list[dict[str, Any]] = []; excluded = Counter()
    for row in rows:
        symbol = str(row.get("symbol") or "").strip().upper()
        exchange = str(row.get("exchange") or "").strip().upper()
        asset_class = str(row.get("class") or row.get("asset_class") or "").lower()
        if not symbol or symbol in seen:
            excluded["DUPLICATE_OR_INVALID_SYMBOL"] += 1; continue
        seen.add(symbol)
        record = {"symbol": symbol, "asset_id": row.get("id"), "status": row.get("status"),
                  "asset_class": asset_class, "exchange": exchange, "tradable": row.get("tradable"),
                  "shortable": row.get("shortable"), "fractionable": row.get("fractionable"),
                  "attributes": row.get("attributes"), "included": False}
        if asset_class not in ("us_equity", "us_equity_common"):
            excluded["NON_US_EQUITY"] += 1
        elif exchange not in ALLOWED_EXCHANGES and not (not exclude_otc and exchange == "OTC"):
            excluded["OTC_OR_UNSUPPORTED_EXCHANGE"] += 1
        elif isinstance(record["attributes"], list) and "ETF" in record["attributes"]:
            excluded["ETF_SEPARATED_FROM_MOMENTUM_UNIVERSE"] += 1
        elif symbol in {"TEST", "TEST1", "DEMO"} or (isinstance(record["attributes"], list) and "TEST" in record["attributes"]):
            excluded["INVALID_OR_TEST_SYMBOL"] += 1
        else:
            record["included"] = True
        included.append(record)
    return included, dict(excluded)


def universe_report(snapshot: dict[str, Any]) -> dict[str, Any]:
    assets = snapshot.get("assets", ())
    return {"raw_assets": snapshot.get("raw_asset_count", 0), "included": sum(item.get("included", False) for item in assets),
            "active": sum(item.get("status") == "active" for item in assets),
            "inactive": sum(item.get("status") == "inactive" for item in assets),
            "exchange_counts": dict(Counter(item.get("exchange") for item in assets)),
            "excluded": sum(1 for item in assets if not item.get("included")),
            "excluded_reasons": snapshot.get("excluded", {}), "otc_policy": "EXCLUDED",
            "provenance": snapshot.get("provenance"), "limitation": snapshot.get("limitation")}
