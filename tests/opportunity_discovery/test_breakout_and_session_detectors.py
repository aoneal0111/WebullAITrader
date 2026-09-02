from decimal import Decimal

from app.opportunity_discovery import DetectionState, FeatureCapabilities, default_registry
from tests.opportunity_discovery.conftest import bar, context


def found(bars, **kwargs):
    return {item.strategy_id: item for item in default_registry().evaluate(context(bars, **kwargs))}


def test_flat_top_hod_and_consolidation_breakout():
    bars = (
        bar(0, 10, 10.5, 9.9, 10.3), bar(1, 10.3, 10.51, 10.2, 10.4),
        bar(2, 10.4, 10.50, 10.25, 10.35), bar(3, 10.35, 10.505, 10.3, 10.4),
        bar(4, 10.4, 10.8, 10.38, 10.7, 2500),
    )
    result = found(bars)
    assert result["HIGH_OF_DAY_BREAKOUT"].state is DetectionState.DETECTED
    assert result["FLAT_TOP_BREAKOUT"].state is DetectionState.DETECTED
    consolidation = (
        bar(0, 10.2, 10.4, 10.15, 10.3), bar(1, 10.3, 10.42, 10.2, 10.35),
        bar(2, 10.35, 10.43, 10.22, 10.3), bar(3, 10.3, 10.41, 10.2, 10.35),
        bar(4, 10.35, 10.8, 10.3, 10.7),
    )
    assert found(consolidation)["CONSOLIDATION_BREAKOUT"].state is DetectionState.DETECTED


def test_compression_and_squeeze_expansion():
    bars = (
        bar(0, 10, 10.5, 9.9, 10.2, 1000), bar(1, 10.2, 10.45, 10.0, 10.3, 1000),
        bar(2, 10.3, 10.48, 10.15, 10.35, 900), bar(3, 10.35, 10.47, 10.25, 10.4, 800),
        bar(4, 10.4, 10.9, 10.35, 10.8, 2000),
    )
    result = found(bars)
    assert result["RANGE_COMPRESSION_BREAKOUT"].state is DetectionState.DETECTED
    assert result["MOMENTUM_SQUEEZE_EXPANSION"].state is DetectionState.DETECTED


def test_breakout_retest_and_failed_breakout_reclaim():
    retest = (
        bar(0, 10, 10.4, 9.9, 10.2), bar(1, 10.2, 10.5, 10.1, 10.4),
        bar(2, 10.4, 10.8, 10.35, 10.7), bar(3, 10.7, 10.72, 10.48, 10.55),
        bar(4, 10.55, 10.9, 10.52, 10.85),
    )
    assert found(retest)["BREAKOUT_RETEST_CONTINUATION"].state is DetectionState.DETECTED
    reclaim = (
        bar(0, 10, 10.5, 9.9, 10.3), bar(1, 10.3, 10.7, 10.2, 10.45),
        bar(2, 10.45, 10.48, 10.1, 10.3), bar(3, 10.3, 10.8, 10.25, 10.75),
    )
    result = found(reclaim)
    assert result["FAILED_BREAKOUT_RECLAIM"].state is DetectionState.DETECTED
    assert result["HOD_RECLAIM"].state is DetectionState.DETECTED


def test_opening_range_premarket_breakout_and_gap_continuation():
    pre = tuple(bar(i, 10, 10.4 + i / 100, 9.9, 10.2, session="PREMARKET") for i in range(3))
    regular = tuple(bar(3 + i, 10.3, 10.5, 10.2, 10.4, session="REGULAR") for i in range(5))
    breakout = (bar(8, 10.4, 10.8, 10.35, 10.7, session="REGULAR"),)
    caps = FeatureCapabilities(prior_close=True)
    result = found(pre + regular + breakout, capabilities=caps, prior_close=Decimal("9.5"))
    assert result["OPENING_RANGE_BREAKOUT"].state is DetectionState.DETECTED
    assert result["PREMARKET_HIGH_BREAKOUT"].state is DetectionState.DETECTED
    assert result["GAP_AND_GO_CONTINUATION"].state is DetectionState.DETECTED
