from dataclasses import FrozenInstanceError
from decimal import Decimal
import pytest
from app.portfolio import *
from tests.portfolio.fixtures import request
def test_request_frozen_slotted_roundtrip():
 value=request();assert not hasattr(value,"__dict__") and PortfolioRequest.from_dict(value.to_dict())==value
 with pytest.raises(FrozenInstanceError):value.account_id="x"
 with pytest.raises(TypeError):value.metadata["x"]=1
def test_position_snapshot_result_roundtrips():
 p=PortfolioPosition("aapl","2","250","200","50","1");s=PortfolioSnapshot("a","500","1000","750","250","750",(p,));r=PortfolioResult("r","a","500","1000","750","250","750",(p,),PortfolioDecision.SUCCESS,(PortfolioCriteriaResult("ok",True,"done"),))
 assert p.symbol=="AAPL" and isinstance(p.market_value,Decimal) and PortfolioPosition.from_dict(p.to_dict())==p and PortfolioSnapshot.from_dict(s.to_dict())==s and PortfolioResult.from_dict(r.to_dict())==r
def test_snapshot_calculation_invariants():
 p=PortfolioPosition("A","1","10","8","2","1")
 with pytest.raises(PortfolioValidationError):PortfolioSnapshot("a","5","5","15","11","16",(p,))
 with pytest.raises(PortfolioValidationError):PortfolioSnapshot("a","5","5","15","10","14",(p,))
def test_failure_result_cannot_expose_values():
 with pytest.raises(PortfolioValidationError):PortfolioResult("r","a","1","0","0","0","1",(),PortfolioDecision.DISABLED,())
