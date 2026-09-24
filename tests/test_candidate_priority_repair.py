from app.live_scanner.coordinator import LiveScannerCoordinator
from app.realtime_scanner.engine import RealtimeScannerEngine
from app.webull.sdk_market_data import _merge_discovery_row


def test_engine_subscription_symbols_preserves_provider_priority() -> None:
    engine = object.__new__(RealtimeScannerEngine)
    engine._subscription_symbols = {
        symbol: symbol
        for symbol in ("WHLR", "IPDN", "LXEH", "AIMD", "AIXI")
    }

    assert engine.subscription_symbols == ("WHLR", "IPDN", "LXEH", "AIMD", "AIXI")


def test_coordinator_retains_management_symbols_before_discretionary_priority() -> None:
    coordinator = object.__new__(LiveScannerCoordinator)
    coordinator._maximum_subscription_channels = 5
    coordinator._retained_channels_source = lambda: ("ZZZA", "WHLR")

    selected = coordinator._effective_channels(
        ("AAAA", "AIMD", "AIXI", "IPDN", "LXEH", "WHLR")
    )

    assert selected == ("ZZZA", "WHLR", "AAAA", "AIMD", "AIXI")


def test_capacity_truncates_priority_order_not_alphabetical_order() -> None:
    coordinator = object.__new__(LiveScannerCoordinator)
    coordinator._maximum_subscription_channels = 4
    coordinator._retained_channels_source = lambda: ()

    selected = coordinator._effective_channels(
        ("WHLR", "IPDN", "LXEH", "AIMD", "AIXI", "AAAA", "AAAB", "ZZZA")
    )

    assert selected == ("WHLR", "IPDN", "LXEH", "AIMD")


def test_priority_deduplication_keeps_first_occurrence_case_insensitively() -> None:
    coordinator = object.__new__(LiveScannerCoordinator)
    coordinator._maximum_subscription_channels = 5
    coordinator._retained_channels_source = lambda: ()

    selected = coordinator._effective_channels(
        ("WHLR", "whlr", "IPDN", "ipdn", "AIMD")
    )

    assert selected == ("WHLR", "IPDN", "AIMD")


def test_sparse_later_source_row_does_not_erase_gainer_fields() -> None:
    merged = _merge_discovery_row(
        {"symbol": "WHLR", "change_ratio": "1.7", "volume": "100"},
        {"symbol": "WHLR", "relative_volume_10d": "20", "change_ratio": ""},
    )

    assert merged["change_ratio"] == "1.7"
    assert merged["volume"] == "100"
    assert merged["relative_volume_10d"] == "20"
