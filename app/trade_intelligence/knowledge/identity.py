"""Deterministic episode and membership identity functions."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime


def canonical_identity(*, symbol: str, trading_date: str, session: str,
                       structural_anchor: str, setup_start: datetime,
                       trigger_price: str, structural_stop: str) -> str:
    value = {
        "version": 1, "symbol": symbol.upper(), "trading_date": trading_date,
        "session": session.upper(), "structural_anchor": structural_anchor,
        "setup_start": setup_start.isoformat(), "trigger_price": trigger_price,
        "structural_stop": structural_stop,
    }
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def episode_id(**kwargs: object) -> str:
    return hashlib.sha256(("ATLAS_KNOWLEDGE_EPISODE_V1|" + canonical_identity(**kwargs)).encode()).hexdigest()


def membership_id(episode: str, strategy: str) -> str:
    return hashlib.sha256(f"ATLAS_KNOWLEDGE_MEMBERSHIP_V1|{episode}|{strategy}".encode()).hexdigest()


def near_key(symbol: str, trading_date: str, session: str, strategy: str, anchor: str) -> str:
    return "|".join((symbol.upper(), trading_date, session.upper(), strategy, anchor))
