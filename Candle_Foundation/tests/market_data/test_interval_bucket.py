from datetime import datetime, timezone

import pytest

from app.market_data.candle_models import TimeFrame
from app.market_data.interval_bucket import bucket_end, bucket_start


@pytest.mark.parametrize(
    ("interval", "expected_hour", "expected_minute"),
    [
        (TimeFrame.ONE_MINUTE, 10, 3),
        (TimeFrame.FIVE_MINUTES, 10, 0),
        (TimeFrame.FIFTEEN_MINUTES, 10, 0),
        (TimeFrame.THIRTY_MINUTES, 10, 0),
        (TimeFrame.ONE_HOUR, 10, 0),
    ],
)
def test_bucket_start_aligns_intraday_intervals(
    interval: TimeFrame,
    expected_hour: int,
    expected_minute: int,
) -> None:
    timestamp = datetime(2026, 7, 28, 10, 3, 42, 183000, tzinfo=timezone.utc)
    result = bucket_start(timestamp, interval)
    assert result == datetime(
        2026,
        7,
        28,
        expected_hour,
        expected_minute,
        tzinfo=timezone.utc,
    )


def test_daily_bucket_uses_local_midnight() -> None:
    timestamp = datetime(2026, 7, 28, 23, 59, 59, tzinfo=timezone.utc)
    assert bucket_start(timestamp, TimeFrame.ONE_DAY) == datetime(
        2026, 7, 28, tzinfo=timezone.utc
    )


def test_bucket_end_is_exclusive_boundary() -> None:
    timestamp = datetime(2026, 7, 28, 10, 3, tzinfo=timezone.utc)
    assert bucket_end(timestamp, TimeFrame.FIVE_MINUTES) == datetime(
        2026, 7, 28, 10, 5, tzinfo=timezone.utc
    )


def test_bucket_rejects_naive_timestamp() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        bucket_start(datetime(2026, 7, 28, 10, 3), TimeFrame.ONE_MINUTE)
