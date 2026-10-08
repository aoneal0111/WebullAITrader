"""Explicit opt-in fixtures for tests that require offline intelligence."""
import pytest

from tests.test_support.historical_intelligence import write_artifact, write_report


@pytest.fixture
def historical_intelligence_artifact(tmp_path, monkeypatch):
    from app.trade_intelligence.decision_intelligence import HistoricalDecisionIntelligence
    artifact = write_artifact(tmp_path / "evidence")
    original = HistoricalDecisionIntelligence.__init__

    def initialize(self, artifact_path=artifact, **kwargs):
        original(self, artifact_path, **kwargs)

    monkeypatch.setattr(HistoricalDecisionIntelligence, "__init__", initialize)
    return artifact


@pytest.fixture
def synthetic_research_report(tmp_path):
    return write_report(tmp_path / "synthetic-report.json")
