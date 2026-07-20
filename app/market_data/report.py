from __future__ import annotations
import json
from app.market_data.recorder import _safe


def market_data_to_json(value: object) -> str:
    return json.dumps(_safe(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def market_data_to_text(value: object) -> str:
    return "DETERMINISTIC MARKET DATA — INFORMATIONAL EVENTS ONLY\n" + market_data_to_json(value) + "\n"
