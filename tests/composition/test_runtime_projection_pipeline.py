from app.composition.runtime_event_sink import CompositeRuntimeEventSink
from app.composition.runtime_projection_pipeline import (
    create_runtime_projection_pipeline,
)
from app.operations_core import OperationsBus


def test_shared_projection_pipeline_exposes_production_sink_order() -> None:
    pipeline = create_runtime_projection_pipeline(
        operations_bus=OperationsBus(),
        account_id="paper",
    )

    assert isinstance(pipeline.sink, CompositeRuntimeEventSink)
    assert pipeline.sinks == (
        pipeline.order_projection,
        pipeline.position_projection,
        pipeline.portfolio_projection,
        pipeline.health_projection,
        pipeline.watchlist_projection,
        pipeline.timeline_projection,
        pipeline.decision_projection,
    )
