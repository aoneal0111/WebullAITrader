from dataclasses import replace
import pytest
from app.backtest_report import *
from tests.backtest_report.helpers import request,source
def test_disabled_reporting_produces_no_report():
    req=request(policy=BacktestReportPolicy(enabled=False));result=BacktestReportRuntime().create(req)
    assert result.status is BacktestReportStatus.DISABLED and result.report is None and result.requested_at is req.requested_at
def test_identity_mismatch_is_rejected():
    req=request();bad=replace(req,identity=BacktestReportIdentity("report-1","other"));result=BacktestReportRuntime().create(bad)
    assert result.status is BacktestReportStatus.REJECTED and result.report is None
def test_wrong_request_type_raises_boundary_validation():
    with pytest.raises(BacktestReportValidationError):BacktestReportRuntime().create(object())
def test_malformed_completed_run_rejected():
    run=replace(source(1),analytics_result=None);result=BacktestReportRuntime().create(request(run));assert result.status is BacktestReportStatus.REJECTED and result.report is None
