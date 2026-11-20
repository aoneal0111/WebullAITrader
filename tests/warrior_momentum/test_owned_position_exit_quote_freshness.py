"""Owned Warrior exits must not depend on last-trade print freshness."""

from datetime import UTC, datetime
from decimal import Decimal as D
from types import SimpleNamespace as NS

import pytest

from app.strategies.warrior_momentum.forward_runtime import (
    WarriorForwardCaptureService,
)


NOW = datetime(2026, 10, 9, 16, 53, tzinfo=UTC)


def evidence(*, quote_age, last_trade_age, bid="2.45", ask="2.46"):
    service = NS(capture_config=NS(quote_stale_after_seconds=D("5")))
    state = NS(latest_exit_evidence=None)
    observation = NS(
        observation=NS(
            timestamp=NOW,
            bid=None if bid is None else D(bid),
            ask=None if ask is None else D(ask),
        ),
        evaluation_timestamp=NOW,
        quote_freshness_seconds=quote_age,
        last_price_freshness_seconds=last_trade_age,
        depth_bids=(),
        depth_asks=(),
        order_flow=None,
    )
    WarriorForwardCaptureService._capture_exit_evidence(
        service, state, observation,
    )
    return state.latest_exit_evidence


def test_fresh_executable_quote_stays_eligible_with_stale_last_trade():
    result = evidence(
        quote_age=D("0.2"), last_trade_age=D("30"),
    )
    assert result.quote_fresh is True
    assert result.best_bid == D("2.45")
    assert result.best_ask == D("2.46")


@pytest.mark.parametrize("age", [None, D("-0.01"), D("5.01")])
def test_missing_future_or_stale_quote_remains_ineligible(age):
    assert evidence(quote_age=age, last_trade_age=D("0")).quote_fresh is False


@pytest.mark.parametrize(
    ("bid", "ask"),
    [(None, "2.46"), ("2.45", None), ("0", "2.46"),
     ("2.47", "2.46")],
)
def test_invalid_or_crossed_book_remains_ineligible(bid, ask):
    assert evidence(
        quote_age=D("0.2"), last_trade_age=D("0"),
        bid=bid, ask=ask,
    ).quote_fresh is False


def test_last_trade_freshness_does_not_rescue_a_stale_quote():
    assert evidence(
        quote_age=D("7"), last_trade_age=D("0"),
    ).quote_fresh is False
