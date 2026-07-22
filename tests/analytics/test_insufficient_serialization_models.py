from dataclasses import FrozenInstanceError
import pytest
from app.analytics import *
from tests.analytics.helpers import entry,request,runtime

def test_empty_allowed_insufficient_valid_summary_and_evaluator_called():
    result=runtime().evaluate(request())
    assert result.status is AnalyticsStatus.INSUFFICIENT_DATA and result.summary.metrics.total_entries==0
    assert result.summary.equity_curve==() and result.warnings

def test_no_usable_data_and_minimum_classified_insufficient():
    result=runtime().evaluate(request((entry(pnl=None,equity=None,fees=None,quantity=None),)))
    assert result.status is AnalyticsStatus.INSUFFICIENT_DATA
    assert runtime(minimum_classified_trades=2).evaluate(request((entry(pnl="1"),))).status is AnalyticsStatus.INSUFFICIENT_DATA

def test_require_entries_is_structural_failure():
    with pytest.raises(AnalyticsValidationError):runtime(require_entries=True).evaluate(request())

def test_policy_validation_roundtrip():
    p=AnalyticsPolicy(enabled=True,minimum_classified_trades=2);assert AnalyticsPolicy.from_dict(p.to_dict())==p
    with pytest.raises(AnalyticsValidationError):AnalyticsPolicy(minimum_classified_trades=0)
    with pytest.raises(AnalyticsValidationError):AnalyticsPolicy(include_equity_curve=False,include_drawdown_curve=True)

def test_public_model_serialization_roundtrips_and_immutability():
    req=request((entry(),));result=runtime().evaluate(req)
    assert AnalyticsRequest.from_dict(req.to_dict())==req and AnalyticsResult.from_dict(result.to_dict())==result
    assert AnalyticsSummary.from_dict(result.summary.to_dict())==result.summary
    assert AnalyticsMetrics.from_dict(result.summary.metrics.to_dict())==result.summary.metrics
    assert EquityPoint.from_dict(result.summary.equity_curve[0].to_dict())==result.summary.equity_curve[0]
    assert DrawdownPoint.from_dict(result.summary.drawdown_curve[0].to_dict())==result.summary.drawdown_curve[0]
    with pytest.raises(FrozenInstanceError):result.status=AnalyticsStatus.DISABLED

def test_serializer_type_boundary():
    from app.analytics import serialize_request
    with pytest.raises(AnalyticsSerializationError):serialize_request({})
