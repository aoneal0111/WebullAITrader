"""Versioned institutional momentum research taxonomy."""

from __future__ import annotations

from .contracts import DetectorAvailability as A, StrategyDefinition, StrategyFamily as F, DETECTOR_VERSION


def _d(identity, family, description, required, optional=(), availability=A.ACTIVE, reason=None, high_risk=False):
    return StrategyDefinition(identity, DETECTOR_VERSION, family, identity.replace("_", " ").title(),
                              description, tuple(required), tuple(optional), availability, reason, high_risk)


STRATEGY_TAXONOMY = (
    _d("MICRO_PULLBACK", F.PULLBACK, "Brief shallow retracement after momentum impulse.", ("completed_bars", "impulse_history", "pullback_history")),
    _d("FIRST_PULLBACK", F.PULLBACK, "First orderly pullback and resumption after an impulse.", ("completed_bars", "impulse_history", "pullback_history")),
    _d("SECOND_PULLBACK_CONTINUATION", F.CONTINUATION, "Second controlled pullback within one lifecycle.", ("pullback_ordinal",), availability=A.INSUFFICIENT_CONTEXT, reason="bounded context does not yet carry authoritative pullback ordinal"),
    _d("HIGHER_LOW_CONTINUATION", F.CONTINUATION, "Higher structural low followed by renewed momentum.", ("completed_bars", "impulse_history", "pullback_history")),
    _d("SHALLOW_PULLBACK_CONTINUATION", F.CONTINUATION, "Retracement no greater than 35% of the impulse.", ("completed_bars", "impulse_history", "pullback_history")),
    _d("DEEP_PULLBACK_RECLAIM", F.RECLAIM, "Deep retracement that regains the impulse midpoint.", ("completed_bars", "impulse_history", "pullback_history")),
    _d("VOLUME_CONTRACTION_PULLBACK", F.PULLBACK, "Pullback average volume contracts from impulse volume.", ("completed_bars", "impulse_history", "pullback_history")),
    _d("MOMENTUM_REACCELERATION", F.CONTINUATION, "Renewed range and velocity after an orderly slowdown.", ("completed_bars", "impulse_history", "pullback_history")),
    _d("HIGH_OF_DAY_BREAKOUT", F.BREAKOUT, "Close-confirmed break of point-in-time session high.", ("completed_bars", "session_hod")),
    _d("FLAT_TOP_BREAKOUT", F.BREAKOUT, "Break above repeated tightly clustered highs.", ("completed_bars",)),
    _d("CONSOLIDATION_BREAKOUT", F.BREAKOUT, "Break from a bounded narrow consolidation.", ("completed_bars",)),
    _d("ASCENDING_BASE_BREAKOUT", F.BREAKOUT, "Break from a base with successively higher lows.", ("completed_bars",)),
    _d("RANGE_COMPRESSION_BREAKOUT", F.COMPRESSION_EXPANSION, "Contracting ranges followed by close-confirmed expansion.", ("completed_bars",)),
    _d("BREAKOUT_RETEST_CONTINUATION", F.CONTINUATION, "Breakout, controlled level retest, then continuation.", ("completed_bars",)),
    _d("PRIOR_RESISTANCE_BREAKOUT", F.BREAKOUT, "Break of an authoritative established prior resistance.", ("prior_day_levels",), availability=A.UNAVAILABLE_FEATURE, reason="authoritative prior-day/reference-level feed is unavailable"),
    _d("OPENING_RANGE_BREAKOUT", F.OPENING_MOMENTUM, "Break of a completed five-bar regular-session opening range.", ("completed_bars", "opening_range")),
    _d("PREMARKET_HIGH_BREAKOUT", F.OPENING_MOMENTUM, "Regular-session break above point-in-time premarket high.", ("completed_bars", "premarket_history")),
    _d("PREMARKET_CONSOLIDATION_BREAKOUT", F.BREAKOUT, "Premarket break from a completed narrow consolidation.", ("completed_bars", "premarket_history")),
    _d("OPENING_DRIVE_CONTINUATION", F.OPENING_MOMENTUM, "Continued expansion during the completed opening drive.", ("completed_bars", "opening_range")),
    _d("VWAP_RECLAIM", F.RECLAIM, "Close-confirmed reclaim of authoritative point-in-time VWAP.", ("authoritative_vwap",), availability=A.UNAVAILABLE_FEATURE, reason="authoritative point-in-time VWAP is unavailable"),
    _d("VWAP_PULLBACK_HOLD", F.PULLBACK, "Pullback holds authoritative point-in-time VWAP.", ("authoritative_vwap",), availability=A.UNAVAILABLE_FEATURE, reason="authoritative point-in-time VWAP is unavailable"),
    _d("FAILED_BREAKOUT_RECLAIM", F.RECLAIM, "Lost breakout area is regained without structural invalidation.", ("completed_bars",)),
    _d("HOD_RECLAIM", F.RECLAIM, "Previously lost session-high region is regained.", ("completed_bars", "session_hod")),
    _d("GAP_AND_GO_CONTINUATION", F.GAP, "Opening gap retains the prior-close reference and continues.", ("completed_bars", "prior_close")),
    _d("POST_GAP_RECLAIM", F.GAP, "Existing pure post-gap flush/reclaim research geometry.", ("completed_bars",), ("relative_volume", "dollar_volume", "spread_percent", "float_shares")),
    _d("RED_TO_GREEN_MOMENTUM", F.REVERSAL_TO_MOMENTUM, "Session reclaims prior close after trading below it.", ("prior_close",), availability=A.INSUFFICIENT_CONTEXT, reason="authoritative prior close is not guaranteed in discovery context"),
    _d("DIP_AND_RIP", F.REVERSAL_TO_MOMENTUM, "Deep but valid impulse retracement followed by range expansion.", ("completed_bars", "impulse_history", "pullback_history")),
    _d("HALT_RESUMPTION_CONTINUATION", F.HALT_RESUMPTION, "Momentum continuation after authoritative halt/resume.", ("halt_resume_facts",), availability=A.UNAVAILABLE_FEATURE, reason="authoritative halt/resume sequence facts are unavailable"),
    _d("MOMENTUM_SQUEEZE_EXPANSION", F.COMPRESSION_EXPANSION, "Multi-bar range squeeze followed by volume/range expansion.", ("completed_bars",)),
    _d("PARABOLIC_CONTINUATION", F.SPECIALIZED, "High-risk parabolic continuation research hypothesis.", ("completed_bars",), availability=A.FUTURE_RESEARCH, reason="requires a separately reviewed high-risk definition", high_risk=True),
)


def taxonomy_by_id():
    return {item.strategy_id: item for item in STRATEGY_TAXONOMY}
