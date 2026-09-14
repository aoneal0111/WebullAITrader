import pytest
from dotenv import load_dotenv

from app.configuration import load_configuration
from app.trade_intelligence.decision_intelligence.entry_timing import (
    EntryIntelligenceConfig,
    HistoricalPaperEntryTimingPolicy,
    OBSERVE_ONLY,
    PAPER_TREATMENT,
)
from app.paper_trade_experiment.harness import PaperExperimentJournal


def _paper(path=None, **overrides):
    values = {
        "WEBULL_TRADING_ENVIRONMENT": "PAPER",
        "ATLAS_HISTORICAL_ENTRY_EXPERIMENT_ENABLED": "true",
    }
    if path is not None:
        values["ATLAS_HISTORICAL_ENTRY_EXPERIMENT_PATH"] = str(path)
    values.update(overrides)
    return values


def test_enabled_experiment_requires_explicit_mode(tmp_path):
    with pytest.raises(ValueError, match="MODE must be explicitly set"):
        load_configuration(_paper(tmp_path / "experiment.sqlite3"))


def test_explicit_observe_only_is_valid_and_does_not_assign(tmp_path):
    path = tmp_path / "experiment.sqlite3"
    configuration = load_configuration(_paper(path, ATLAS_HISTORICAL_ENTRY_EXPERIMENT_MODE=OBSERVE_ONLY))
    assert configuration.historical_entry_experiment_mode == OBSERVE_ONLY

    journal = PaperExperimentJournal(path)
    policy = HistoricalPaperEntryTimingPolicy(
        config=EntryIntelligenceConfig(
            enabled=True, mode=OBSERVE_ONLY, journal_path=str(path)
        ),
        journal=journal,
    )
    try:
        assert journal._connection.execute(
            "SELECT COUNT(*) FROM experiment_assignments"
        ).fetchone()[0] == 0
        assert tuple(journal._connection.execute(
            "SELECT marker_type, historical_entry_experiment_mode "
            "FROM experiment_runtime_markers"
        ).fetchone()) == ("POLICY_INITIALIZED", OBSERVE_ONLY)
    finally:
        policy.close()
        journal.close()


def test_paper_treatment_requires_paper_and_explicit_path(tmp_path):
    with pytest.raises(ValueError):
        load_configuration(_paper(
            tmp_path / "experiment.sqlite3",
            WEBULL_TRADING_ENVIRONMENT="TEST",
            ATLAS_HISTORICAL_ENTRY_EXPERIMENT_MODE=PAPER_TREATMENT,
        ))
    with pytest.raises(ValueError, match="PATH must be explicitly set"):
        load_configuration(_paper(
            ATLAS_HISTORICAL_ENTRY_EXPERIMENT_MODE=PAPER_TREATMENT
        ))


def test_paper_treatment_uses_explicit_resolved_path_and_records_snapshot(tmp_path):
    path = tmp_path / "session" / "experiment.sqlite3"
    configuration = load_configuration(_paper(
        path, ATLAS_HISTORICAL_ENTRY_EXPERIMENT_MODE=PAPER_TREATMENT
    ))
    assert configuration.historical_entry_experiment_path == path.resolve()

    policy = HistoricalPaperEntryTimingPolicy(config=EntryIntelligenceConfig(
        enabled=True, mode=PAPER_TREATMENT, journal_path=str(path),
        trading_environment="PAPER", warrior_forward_paper_enabled=True,
    ))
    try:
        row = policy._journal._connection.execute(
            "SELECT trading_environment, warrior_forward_paper_enabled, "
            "historical_entry_experiment_mode, historical_entry_experiment_path "
            "FROM experiment_runtime_markers ORDER BY startup_timestamp DESC"
        ).fetchone()
        assert tuple(row) == (
            "PAPER", 1, PAPER_TREATMENT, str(path.resolve())
        )
    finally:
        policy.close()


def test_invalid_mode_and_live_treatment_fail_visibly(tmp_path):
    with pytest.raises(ValueError, match="MODE is malformed"):
        load_configuration(_paper(
            tmp_path / "experiment.sqlite3",
            ATLAS_HISTORICAL_ENTRY_EXPERIMENT_MODE="not-a-mode",
        ))
    with pytest.raises(ValueError, match="requires WEBULL_TRADING_ENVIRONMENT=PAPER"):
        load_configuration(_paper(
            tmp_path / "experiment.sqlite3",
            WEBULL_TRADING_ENVIRONMENT="LIVE",
            ATLAS_HISTORICAL_ENTRY_EXPERIMENT_MODE=PAPER_TREATMENT,
        ))


def test_disabled_default_remains_valid():
    configuration = load_configuration({})
    assert not configuration.historical_entry_experiment_enabled


def test_launcher_process_environment_wins_over_dotenv(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text(
        "ATLAS_HISTORICAL_ENTRY_EXPERIMENT_ENABLED=false\n"
        "ATLAS_HISTORICAL_ENTRY_EXPERIMENT_MODE=OBSERVE_ONLY\n"
        "ATLAS_HISTORICAL_ENTRY_EXPERIMENT_PATH=dotenv.sqlite3\n",
        encoding="utf-8",
    )
    process_path = tmp_path / "process.sqlite3"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("WEBULL_TRADING_ENVIRONMENT", "PAPER")
    monkeypatch.setenv("ATLAS_HISTORICAL_ENTRY_EXPERIMENT_ENABLED", "true")
    monkeypatch.setenv("ATLAS_HISTORICAL_ENTRY_EXPERIMENT_MODE", PAPER_TREATMENT)
    monkeypatch.setenv("ATLAS_HISTORICAL_ENTRY_EXPERIMENT_PATH", str(process_path))
    load_dotenv(override=False)

    configuration = load_configuration()
    assert configuration.historical_entry_experiment_enabled is True
    assert configuration.historical_entry_experiment_mode == PAPER_TREATMENT
    assert configuration.historical_entry_experiment_path == process_path.resolve()
