from dataclasses import FrozenInstanceError
import pytest
from app.trade_journal_batch import *
from app.trade_journal_batch.serializers import serialize_result
from tests.trade_journal_batch.helpers import request,runtime
def test_enums_and_policy_roundtrip():
    assert len(TradeJournalBatchStatus)==6 and len(TradeJournalBatchItemStatus)==4 and len(TradeJournalBatchFailureMode)==2
    p=TradeJournalBatchPolicy(failure_mode=TradeJournalBatchFailureMode.CONTINUE_ON_FAILURE,allow_empty=True)
    assert TradeJournalBatchPolicy.from_dict(p.to_dict())==p
def test_models_frozen_and_roundtrip():
    req=request(1);result=runtime()[0].run(req)
    assert TradeJournalBatchRequest.from_dict(req.to_dict())==req and TradeJournalBatchResult.from_dict(result.to_dict())==result
    assert serialize_result(result)==result.to_dict()
    with pytest.raises(FrozenInstanceError):result.status=TradeJournalBatchStatus.FAILED
def test_invalid_identity_policy_and_progress():
    with pytest.raises(TradeJournalBatchValidationError):TradeJournalBatchIdentity("","journal")
    with pytest.raises(TradeJournalBatchValidationError):TradeJournalBatchIdentity("batch","journal","")
    with pytest.raises(TradeJournalBatchValidationError):TradeJournalBatchPolicy(failure_mode="STOP_ON_FAILURE")
    with pytest.raises(TradeJournalBatchValidationError):TradeJournalBatchProgress(1,1,1,0,0)
def test_serializer_rejects_wrong_type():
    with pytest.raises(TradeJournalBatchSerializationError):serialize_result({})
