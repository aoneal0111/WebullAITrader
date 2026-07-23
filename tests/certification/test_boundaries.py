from dataclasses import fields,is_dataclass
from pathlib import Path
import pytest
MODULES=("account_information","positions","market_data","order_placement","order_status","open_orders","order_cancellation","session")
def test_runtime_layers_do_not_import_webull_or_transport_implementations():
 violations=[]
 for name in MODULES:
  for path in Path("app",name).glob("*.py"):
   text=path.read_text(encoding="utf-8").lower()
   for term in ("app.webull","import httpx","import requests","from app.httpx_transport","from app.webull_transport"):
    if term in text:violations.append((str(path),term))
 assert not violations
def test_runtime_models_do_not_expose_transport_or_webull_fields():
 prohibited=("webull","transport","http","header","cookie","token","payload")
 runtime_models={"AccountInformationRequest","BrokerNeutralAccountInformation","AccountInformationResult","PositionModel","PositionsRequest","PositionsResult","QuoteModel","MarketDataRequest","MarketDataResult","OrderRequestModel","OrderPlacementRequest","BrokerOrderAcknowledgement","OrderPlacementResult","OrderStatusRequest","BrokerOrderStatusSnapshot","OrderStatusResult","OpenOrdersRequest","OpenOrderSnapshot","OpenOrdersResult","OrderCancellationRequest","BrokerOrderCancellationAcknowledgement","OrderCancellationResult","SessionRequest","Session","SessionSnapshot"}
 violations=[]
 for name in MODULES:
  module=__import__(f"app.{name}.models",fromlist=["models"])
  for value in vars(module).values():
   if isinstance(value,type) and value.__name__ in runtime_models and is_dataclass(value):
    for field in fields(value):
     if any(term in field.name.lower() for term in prohibited):violations.append((value.__name__,field.name))
 assert not violations
def test_no_retry_polling_or_hidden_adapter_state_in_runtime_sources():
 text="\n".join(Path("app",name,"runtime.py").read_text(encoding="utf-8").lower() for name in MODULES if Path("app",name,"runtime.py").exists())
 assert "retry" not in text and "poll" not in text and "while " not in text
def test_existing_webull_gateway_remains_non_instantiating_protocol_boundary():
 from app.webull_gateway import WebullGateway
 with pytest.raises(TypeError):WebullGateway()
