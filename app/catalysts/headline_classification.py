from __future__ import annotations

import re

from app.momentum_scanner.models import CatalystType


_GENERIC_OR_EDITORIAL = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bmarket (?:recap|summary|update|wrap)\b",
        r"\b(?:stocks?|tickers?) to watch\b",
        r"\bwhy .* stock (?:rose|fell|jumped|dropped|is (?:up|down))\b",
        r"\btechnical analysis\b|\bchart analysis\b",
        r"\b(?:top|best) \d+ .*stocks?\b",
        r"\bshould you (?:buy|sell|invest)\b",
        r"\bopinion\b|\bwhat investors should know\b",
        r"\bearnings (?:preview|call|call transcript|date|scheduled)\b|"
        r"\b(?:will|expected to) report earnings\b",
        r"\b(?:rumou?r|reportedly|could|may|might)\b.*"
        r"\b(?:acquire|acquisition|merge|merger|buyout)\b",
    )
)

_CLASSIFIERS: tuple[tuple[CatalystType, tuple[re.Pattern[str], ...]], ...] = (
    (
        CatalystType.EARNINGS,
        tuple(
            map(
                re.compile,
                (
                    r"\bearnings\b.*\b(?:reports?|results?|beats?|misses?)\b",
                    r"\b(?:reports?|announces?|posts?)\b.*\bearnings\b",
                    r"\b(?:reports?|announces?|posts?)\b.*"
                    r"\b(?:q[1-4]|quarter(?:ly)?|annual|full[- ]year) results?\b",
                    r"\b(?:q[1-4]|quarter(?:ly)?|annual|full[- ]year) results?\b",
                ),
                (re.IGNORECASE,) * 4,
            )
        ),
    ),
    (
        CatalystType.GUIDANCE,
        tuple(
            re.compile(pattern, re.IGNORECASE)
            for pattern in (
                r"\b(?:raises?|cuts?|lowers?|reaffirms?|issues?|updates?)\b.*"
                r"\b(?:guidance|outlook)\b",
                r"\b(?:guidance|outlook)\b.*"
                r"\b(?:raised|cut|lowered|reaffirmed|issued|updated)\b",
            )
        ),
    ),
    (
        CatalystType.FDA,
        tuple(
            re.compile(pattern, re.IGNORECASE)
            for pattern in (
                r"\bFDA\b.*\b(?:approves?|grants? approval|clears?|authorizes?)\b",
                r"\b(?:receives?|wins?|granted)\b.*\bFDA\b.*"
                r"\b(?:approval|clearance|authorization)\b",
            )
        ),
    ),
    (
        CatalystType.CLINICAL_TRIAL,
        tuple(
            re.compile(pattern, re.IGNORECASE)
            for pattern in (
                r"\b(?:clinical|phase [123]) (?:trial|study)\b.*"
                r"\b(?:results?|data|meets?|achieves?|endpoint|enrollment)\b",
                r"\b(?:results?|data|meets?|achieves?)\b.*"
                r"\b(?:clinical|phase [123]) (?:trial|study)\b",
            )
        ),
    ),
    (
        CatalystType.ACQUISITION,
        tuple(
            re.compile(pattern, re.IGNORECASE)
            for pattern in (
                r"\bto acquire\b|\bacquires?\b|\bto be acquired\b",
                r"\b(?:announces?|agrees? to|completes?|closes?)\b.*"
                r"\b(?:acquisition|merger|buyout)\b",
                r"\bmerges? with\b",
            )
        ),
    ),
    (
        CatalystType.CONTRACT,
        tuple(
            re.compile(pattern, re.IGNORECASE)
            for pattern in (
                r"\b(?:wins?|awarded|receives?|secures?)\b.*"
                r"\b(?:contract|purchase order|order|award)\b",
                r"\b(?:contract|purchase order)\b.*"
                r"\b(?:awarded|signed|secured|received)\b",
            )
        ),
    ),
    (
        CatalystType.PARTNERSHIP,
        (
            re.compile(
                r"\b(?:announces?|enters?|forms?|signs?)\b.*"
                r"\b(?:partnership|collaboration|alliance)\b|"
                r"\b(?:partners?|collaborates?) with\b",
                re.IGNORECASE,
            ),
        ),
    ),
    (
        CatalystType.SEC_FILING,
        (
            re.compile(
                r"\bSEC filing\b|\bfiles?\b.*"
                r"\b(?:8-K|10-Q|10-K|S-1|S-3|13D|13G)\b",
                re.IGNORECASE,
            ),
        ),
    ),
)

_STRONG_OTHER = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?:declares?|increases?|raises?)\b.*\bdividend\b",
        r"\b(?:patent granted|granted (?:a )?patent)\b",
        r"\b(?:launches?|receives?)\b.*\b(?:product|certification)\b",
    )
)

_CNBC_REJECTIONS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?:analysts?|cramer)\b",
        r"\b(?:upgrade[sd]?|downgrade[sd]?|price target|rating)\b",
        r"\b(?:reportedly|rumou?r|in talks|could|may|might|sources say)\b",
        r"\b(?:preview|what to expect|expected to|set to report)\b",
        r"\b(?:stock|shares)\b.*\b(?:rises?|falls?|jumps?|drops?|soars?|"
        r"slides?|pops?|tanks?|plunges?)\b",
        r"\b(?:rises?|falls?|jumps?|drops?|soars?|slides?|pops?|tanks?|"
        r"plunges?)\b.*\b(?:stock|shares)\b",
    )
)

_CNBC_ADDITIONAL = (
    (
        CatalystType.FDA,
        re.compile(r"\bFDA\b.*\b(?:rejects?|denies?|declines?)\b", re.IGNORECASE),
    ),
    (
        CatalystType.ACQUISITION,
        re.compile(
            r"\b(?:agrees? to buy|to buy|buys?)\b.*\b(?:company|corp|inc)?\b",
            re.IGNORECASE,
        ),
    ),
)


def classify_catalyst_headline(headline: str) -> CatalystType | None:
    """Preserve the conservative Yahoo classification contract."""

    normalized = " ".join(str(headline).split())
    if not normalized or any(
        pattern.search(normalized) for pattern in _GENERIC_OR_EDITORIAL
    ):
        return None
    for catalyst_type, patterns in _CLASSIFIERS:
        if any(pattern.search(normalized) for pattern in patterns):
            return catalyst_type
    if any(pattern.search(normalized) for pattern in _STRONG_OTHER):
        return CatalystType.OTHER
    return None


def classify_cnbc_headline(headline: str) -> CatalystType | None:
    """Apply CNBC-specific editorial and speculation rejection before classifying."""

    normalized = " ".join(str(headline).split())
    if not normalized or any(pattern.search(normalized) for pattern in _CNBC_REJECTIONS):
        return None
    classified = classify_catalyst_headline(normalized)
    if classified is not None:
        return classified
    for catalyst_type, pattern in _CNBC_ADDITIONAL:
        if pattern.search(normalized):
            return catalyst_type
    return None


__all__ = ["classify_catalyst_headline", "classify_cnbc_headline"]
