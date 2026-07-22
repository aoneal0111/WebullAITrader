from dataclasses import FrozenInstanceError
from decimal import Decimal
import pytest
from app.execution_planner import *
from app.order_placement import OrderSide,OrderType,TimeInForce
from tests.execution_planner.fixtures import request
def test_request_frozen_slotted_roundtrip():
 value=request();assert not hasattr(value,"__dict__") and ExecutionPlanRequest.from_dict(value.to_dict())==value
 with pytest.raises(FrozenInstanceError):value.request_id="x"
 with pytest.raises(TypeError):value.metadata["x"]=1
def test_instruction_plan_result_roundtrip():
 i=ExecutionInstruction("a","aapl",OrderSide.BUY,"2",OrderType.LIMIT,TimeInForce.DAY,"10");p=ExecutionPlan("r",(i,));r=ExecutionPlanResult("r",ExecutionPlanDecision.PLANNED,p,(ExecutionPlanCriteriaResult("ok",True,"done"),),"v")
 assert i.symbol=="AAPL" and isinstance(i.quantity,Decimal) and ExecutionInstruction.from_dict(i.to_dict())==i and ExecutionPlan.from_dict(p.to_dict())==p and ExecutionPlanResult.from_dict(r.to_dict())==r
def test_exactly_one_instruction_and_price_rules():
 with pytest.raises(ExecutionPlannerValidationError):ExecutionPlan("r",())
 with pytest.raises(ExecutionPlannerValidationError):ExecutionInstruction("a","A",OrderSide.BUY,1,OrderType.LIMIT,TimeInForce.DAY)
 with pytest.raises(ExecutionPlannerValidationError):ExecutionInstruction("a","A",OrderSide.BUY,0,OrderType.MARKET,TimeInForce.DAY)
def test_raw_strategy_decision_not_accepted():
 with pytest.raises(ExecutionPlannerValidationError):ExecutionPlanRequest("r",object(),object())
