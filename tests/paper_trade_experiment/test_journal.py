from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.momentum_scanner import (
    CatalystStatus,
    CatalystType,
    MomentumScannerConfig,
    ScannerObservation,
    evaluate_candidate,
)
from app.paper_trade_analysis import build_report, main
from app.paper_trade_experiment import PaperTradeExperimentJournal


NOW = datetime(2026, 8, 20, 14, 30, tzinfo=UTC)


def observation(
    *,
    symbol: str = "BIVI",
    catalyst: CatalystType = CatalystType.NONE,
    status: CatalystStatus = CatalystStatus.FALSE,
    sources: tuple[str, ...] = (),
) -> ScannerObservation:
    return ScannerObservation(
        symbol=symbol,
        timestamp=NOW,
        price=Decimal("5"),
        previous_close=Decimal("4"),
        current_volume=Decimal("2000000"),
        average_30_day_volume=Decimal("200000"),
        float_shares=Decimal("5000000"),
        bid=Decimal("4.99"),
        ask=Decimal("5.01"),
        catalyst=catalyst,
        catalyst_headline=(None if catalyst is CatalystType.NONE else "Structured event"),
        catalyst_status=status,
        catalyst_source=(sources[0] if sources else None),
        catalyst_published_at=(NOW - timedelta(minutes=5) if sources else None),
        catalyst_source_url=("https://example.test/story?api_key=secret" if sources else None),
        corroborating_sources=sources,
        catalyst_evidence_count=len(sources),
        catalyst_event_count=int(bool(sources)),
        tradable=True,
        halted=False,
    )


def test_technical_qualification_is_independent_but_normal_rule_stays_strict() -> None:
    decision = evaluate_candidate(
        observation(), MomentumScannerConfig.conservative_v1(),
    )

    assert decision.qualified is False
    assert decision.technical_qualifies_without_catalyst is True
    assert decision.failed_rules == ("news_catalyst",)
    assert decision.technical_failed_rules == ()
    assert decision.cohort_flags == ("B_TECHNICAL_ONLY", "C_NO_CATALYST")


def test_cohorts_include_corroborated_and_structured_primary() -> None:
    decision = evaluate_candidate(
        observation(
            catalyst=CatalystType.SEC_FILING,
            status=CatalystStatus.TRUE,
            sources=("SEC_EDGAR", "WEBULL_SEC"),
        )
    )

    assert decision.qualified is True
    assert decision.cohort_flags == (
        "A_STRICT_CATALYST",
        "B_TECHNICAL_ONLY",
        "D_CORROBORATED_CATALYST",
        "E_STRONG_PRIMARY_CATALYST",
    )


def test_bivi_counterfactual_is_recorded_without_fake_fill(tmp_path) -> None:
    journal = PaperTradeExperimentJournal(tmp_path / "experiment.sqlite3")
    record = journal.record_candidate(evaluate_candidate(
        observation(), MomentumScannerConfig.conservative_v1(),
    ))

    assert record.features["technical_qualifies_without_catalyst"] is True
    assert record.features["normal_qualifies"] is False
    assert record.features["failed_rules"] == ["news_catalyst"]
    assert record.features["counterfactual_reference_price"] == "5"
    assert record.execution["paper_trade_executed"] is False
    assert "average_fill_price" not in record.execution
    with pytest.raises(ValueError, match="normal strategy did not qualify"):
        journal.record_submission(
            record.candidate_id,
            requested_quantity=Decimal("10"), order_type="MARKET",
            submitted_price=None,
        )


def test_future_labels_and_mfe_mae_are_separate_from_features(tmp_path) -> None:
    journal = PaperTradeExperimentJournal(tmp_path / "experiment.sqlite3")
    record = journal.record_candidate(evaluate_candidate(observation()))
    original_features = dict(record.features)

    journal.observe_price("BIVI", NOW + timedelta(minutes=1), Decimal("5.50"))
    journal.observe_price("BIVI", NOW + timedelta(minutes=5), Decimal("4.50"))
    journal.observe_price("BIVI", NOW + timedelta(minutes=15), Decimal("5.25"))
    journal.observe_price("BIVI", NOW + timedelta(minutes=30), Decimal("6"))
    labeled = journal.get(record.candidate_id)

    assert labeled.features == original_features
    assert Decimal(labeled.labels["return_after_1m"]) == Decimal("0.1")
    assert Decimal(labeled.labels["return_after_5m"]) == Decimal("-0.1")
    assert Decimal(labeled.labels["return_after_15m"]) == Decimal("0.05")
    assert Decimal(labeled.labels["return_after_30m"]) == Decimal("0.2")
    assert Decimal(labeled.labels["mfe"]) == Decimal("0.2")
    assert Decimal(labeled.labels["mae"]) == Decimal("-0.1")
    assert labeled.labels["outcome_status"] == "COMPLETE"


def test_decision_snapshot_identity_is_content_addressed_and_recovers_after_restart(
    tmp_path,
) -> None:
    path = tmp_path / "experiment.sqlite3"
    journal = PaperTradeExperimentJournal(path)
    decision = evaluate_candidate(observation())
    first = journal.record_candidate(
        decision,
        strategy_version="strategy-7",
        model_version="model-3",
    )

    duplicate = journal.record_candidate(
        decision,
        strategy_version="strategy-7",
        model_version="model-3",
    )
    changed = journal.record_candidate(
        replace(decision, score=decision.score + 1),
        strategy_version="strategy-7",
        model_version="model-3",
    )

    assert duplicate.candidate_id == first.candidate_id
    assert changed.candidate_id != first.candidate_id

    recovered = PaperTradeExperimentJournal(path).get(first.candidate_id)
    assert recovered.features["strategy_version"] == "strategy-7"
    assert recovered.features["model_version"] == "model-3"
    assert recovered == first


def test_same_timestamp_and_price_with_changed_market_snapshot_gets_new_identity(
    tmp_path,
) -> None:
    journal = PaperTradeExperimentJournal(tmp_path / "experiment.sqlite3")

    first_observation = observation()
    second_observation = replace(
        first_observation,
        current_volume=first_observation.current_volume + Decimal("1"),
    )

    first_decision = evaluate_candidate(first_observation)
    second_decision = evaluate_candidate(second_observation)

    assert second_decision.symbol == first_decision.symbol
    assert second_decision.timestamp == first_decision.timestamp
    assert second_decision.price == first_decision.price
    assert second_decision.current_volume != first_decision.current_volume

    first = journal.record_candidate(first_decision)
    second = journal.record_candidate(second_decision)

    assert second.candidate_id != first.candidate_id
    assert second.features["current_volume"] != first.features["current_volume"]


def test_live_or_ambiguous_execution_is_impossible(tmp_path) -> None:
    journal = PaperTradeExperimentJournal(tmp_path / "experiment.sqlite3")
    decision = evaluate_candidate(
        observation(catalyst=CatalystType.EARNINGS, status=CatalystStatus.TRUE)
    )
    with pytest.raises(ValueError, match="PAPER or TEST"):
        journal.record_candidate(decision, execution_environment="LIVE")
    record = journal.record_candidate(decision, execution_environment="TEST")
    with pytest.raises(PermissionError, match="LIVE_TRADING_ENABLED"):
        journal.record_submission(
            record.candidate_id, requested_quantity=Decimal("10"),
            order_type="MARKET", submitted_price=None,
            live_trading_enabled=True,
        )


def test_duplicate_prevention_partial_fill_and_exit_recording(tmp_path) -> None:
    journal = PaperTradeExperimentJournal(tmp_path / "experiment.sqlite3")
    decision = evaluate_candidate(
        observation(catalyst=CatalystType.EARNINGS, status=CatalystStatus.TRUE)
    )
    record = journal.record_candidate(decision)
    submitted = journal.record_submission(
        record.candidate_id, requested_quantity=Decimal("10"),
        order_type="LIMIT", submitted_price=Decimal("5"),
    )
    duplicate = journal.record_submission(
        record.candidate_id, requested_quantity=Decimal("10"),
        order_type="LIMIT", submitted_price=Decimal("5"),
        client_order_id=submitted.execution["client_order_id"],
    )
    assert duplicate == submitted

    partial = journal.record_fill(
        record.candidate_id, fill_id="fill-1", quantity=Decimal("4"),
        price=Decimal("5.02"), timestamp=NOW + timedelta(seconds=2),
    )
    assert partial.execution["state"] == "PARTIALLY_FILLED"
    assert partial.execution["fill_quantity"] == "4"
    filled = journal.record_fill(
        record.candidate_id, fill_id="fill-2", quantity=Decimal("6"),
        price=Decimal("5.04"), timestamp=NOW + timedelta(seconds=3),
    )
    assert filled.execution["state"] == "FILLED"
    assert Decimal(filled.execution["average_fill_price"]) == Decimal("5.032")
    closed = journal.record_exit(
        record.candidate_id, exit_price=Decimal("5.50"),
        exit_reason="TARGET", timestamp=NOW + timedelta(minutes=3),
    )
    assert closed.execution["state"] == "CLOSED"
    assert Decimal(closed.execution["realized_pnl"]) == Decimal("4.680")
    assert closed.execution["holding_seconds"] == 178


def test_source_url_is_sanitized_and_secrets_are_not_persisted(tmp_path) -> None:
    journal = PaperTradeExperimentJournal(tmp_path / "experiment.sqlite3")
    record = journal.record_candidate(evaluate_candidate(observation(
        catalyst=CatalystType.EARNINGS,
        status=CatalystStatus.TRUE,
        sources=("WEBULL_EARNINGS",),
    )))
    raw = (tmp_path / "experiment.sqlite3").read_bytes()
    assert record.features["source_url"] == "https://example.test/story"
    assert b"api_key" not in raw
    assert b"secret" not in raw


def test_analysis_report_and_command(tmp_path, capsys) -> None:
    path = tmp_path / "experiment.sqlite3"
    journal = PaperTradeExperimentJournal(path)
    decision = evaluate_candidate(observation())
    journal.record_candidate(decision)
    journal.observe_price("BIVI", NOW + timedelta(minutes=30), Decimal("5.50"))

    report = build_report(journal.records())
    assert report["cohorts"]["B_TECHNICAL_ONLY"]["sample_count"] == 1
    assert Decimal(report["cohorts"]["C_NO_CATALYST"]["mean_return"]) == Decimal("0.1")
    assert "significance" in report["disclaimer"]
    assert main(["--journal", str(path), "--json"]) == 0
    output = capsys.readouterr().out
    assert '"actual_paper_trades"' in output
    assert '"B_TECHNICAL_ONLY"' in output


def test_analysis_command_is_read_only_for_missing_journal(tmp_path) -> None:
    missing = tmp_path / "missing.sqlite3"
    with pytest.raises(FileNotFoundError):
        main(["--journal", str(missing)])
    assert not missing.exists()

def test_pending_candidate_can_complete_after_old_35_minute_cutoff(tmp_path) -> None:
    journal = PaperTradeExperimentJournal(tmp_path / "experiment.sqlite3")
    decision = evaluate_candidate(observation())
    record = journal.record_candidate(decision)

    # Simulate a symbol that receives no usable follow-up price until well
    # after the previous 30m + 5m query cutoff.
    changed = journal.observe_price(
        "BIVI",
        NOW + timedelta(minutes=40),
        Decimal("5.50"),
    )

    labeled = journal.get(record.candidate_id)

    assert changed == 1
    assert Decimal(labeled.labels["price_after_1m"]) == Decimal("5.50")
    assert Decimal(labeled.labels["price_after_5m"]) == Decimal("5.50")
    assert Decimal(labeled.labels["price_after_15m"]) == Decimal("5.50")
    assert Decimal(labeled.labels["price_after_30m"]) == Decimal("5.50")
    assert Decimal(labeled.labels["return_after_30m"]) == Decimal("0.1")
    assert labeled.labels["outcome_status"] == "COMPLETE"
