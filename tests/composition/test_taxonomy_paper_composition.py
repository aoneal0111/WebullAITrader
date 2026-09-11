from app.opportunity_discovery import MultiStrategyExecutionAdapter
from app.trade_intelligence.taxonomy_paper_bridge import TaxonomyPaperExecutionBridge
from app.strategies.warrior_momentum.forward_runtime import WarriorForwardCaptureService
from app.strategies.warrior_momentum.autonomous_paper import lifecycle_identity, opportunity_identity
from app.strategies.warrior_momentum.forward_store import ForwardCaptureStore
from app.strategies.warrior_momentum.forward_queue import ForwardCaptureWriter
from tests.warrior_momentum.test_forward_capture import point


def _service(tmp_path):
    store = ForwardCaptureStore(tmp_path / "taxonomy-forward.sqlite3")
    writer = ForwardCaptureWriter(store, flush_interval_seconds=0.01)
    service = WarriorForwardCaptureService(
        store, writer,
        taxonomy_execution_bridge=TaxonomyPaperExecutionBridge(
            MultiStrategyExecutionAdapter()
        ),
    )
    return store, writer, service


def test_paper_taxonomy_candidate_reaches_existing_signal_seam(tmp_path):
    store, writer, service = _service(tmp_path)
    try:
        value = point()
        legacy = service.runtime.discover(
            value.observation, value.bars, session=value.session,
        )
        projected, signal = service._taxonomy_execution_bridge.evaluate(
            value, legacy, None, False, service.runtime,
            service.capture_config.quote_stale_after_seconds,
        )
        assert projected is not None
        assert signal is not None
        assert signal.execution_authorized is False
        assert signal.risk_per_share > 0
        assert signal.taxonomy_strategy_id in signal.taxonomy_strategy_memberships
        assert signal.taxonomy_strategy_memberships
        assert signal.taxonomy_opportunity_id
        assert signal.taxonomy_opportunity_anchor
        assert signal.taxonomy_execution_identity
        assert lifecycle_identity(signal) == signal.taxonomy_execution_identity
        assert opportunity_identity(signal) == signal.taxonomy_opportunity_id
        assert signal.stop_price == projected.setup.stop_price
    finally:
        writer.close()


def test_legacy_signal_wins_same_observation_without_duplicate_authority(tmp_path):
    store, writer, service = _service(tmp_path)
    try:
        value = point()
        legacy = service.runtime.discover(
            value.observation, value.bars, session=value.session,
        )
        _assessed, legacy_signal = service.runtime.assess_entry(legacy)
        assert legacy_signal is not None
        projected, taxonomy_signal = service._taxonomy_execution_bridge.evaluate(
            value, legacy, legacy_signal, False, service.runtime,
            service.capture_config.quote_stale_after_seconds,
        )
        assert projected is None
        assert taxonomy_signal is None
    finally:
        writer.close()


def test_taxonomy_adapter_is_injected_only_as_an_optional_service_dependency(tmp_path):
    store, writer, service = _service(tmp_path)
    try:
        assert service._taxonomy_execution_bridge is not None
        without_adapter = WarriorForwardCaptureService(store, writer)
        assert without_adapter._taxonomy_execution_bridge is None
    finally:
        writer.close()
