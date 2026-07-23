from dataclasses import FrozenInstanceError
from decimal import Decimal
import pytest
from app.account_information import *
from tests.account_information.fixtures import request


def test_request_is_frozen_slotted_and_roundtrips():
    value=request(); assert not hasattr(value,"__dict__"); assert AccountInformationRequest.from_dict(value.to_dict())==value
    with pytest.raises(FrozenInstanceError): value.request_id="changed"
    with pytest.raises(TypeError): value.metadata["x"]=1


def test_account_normalizes_decimal_and_currency_and_roundtrips():
    value=BrokerNeutralAccountInformation("a","CASH","ACTIVE","1.20",Decimal("2"),3,"usd")
    assert value.currency=="USD" and all(isinstance(x,Decimal) for x in (value.buying_power,value.cash_balance,value.equity))
    assert BrokerNeutralAccountInformation.from_dict(value.to_dict())==value


def test_result_roundtrip_and_success_property():
    result=AccountInformationResult("r","s",AccountInformationDecision.SUCCESS,"a","CASH","ACTIVE","1","2","3","USD",(AccountInformationCriteriaResult("ok",True,"passed"),))
    assert result.success and AccountInformationResult.from_dict(result.to_dict())==result


@pytest.mark.parametrize("value", ["NaN","Infinity",-1,True,object()])
def test_invalid_money_rejected(value):
    with pytest.raises(AccountInformationValidationError): BrokerNeutralAccountInformation("a","t","s",value,0,0,"USD")


def test_failure_result_cannot_expose_account_data():
    with pytest.raises(AccountInformationValidationError): AccountInformationResult("r","s",AccountInformationDecision.GATEWAY_FAILURE,"a","","",0,0,0,"",())
