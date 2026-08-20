from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import re

from app.momentum_scanner.models import CatalystType


_NON_WORD = re.compile(r"[^a-z0-9]+")


def canonical_headline_event_id(
    symbol: str,
    catalyst_type: CatalystType,
    headline: str,
    published_at: datetime,
) -> str:
    """Build a provider-neutral ID for the same normalized dated headline.

    This deliberately does not attempt fuzzy event matching. Other headline and
    press-release providers can use the same function when they carry the same
    syndicated title; stronger provider IDs (for example an SEC accession) take
    precedence whenever they are available.
    """

    normalized_headline = _NON_WORD.sub(" ", headline.casefold()).strip()
    payload = "\x1f".join(
        (
            symbol.strip().upper().replace(".", "-"),
            catalyst_type.value,
            published_at.date().isoformat(),
            normalized_headline,
        )
    ).encode("utf-8")
    return "headline-event:" + sha256(payload).hexdigest()


__all__ = ["canonical_headline_event_id"]
