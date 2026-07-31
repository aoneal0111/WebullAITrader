from datetime import UTC,datetime
from decimal import Decimal
import json,pytest
from app.configuration import load_configuration,TradingEnvironment
from app.operations.redaction import redact
from app.operations.emergency_stop import EmergencyStopStore
from app.operations.health import ReadinessInputs,liveness,readiness
from app.market_data.durable_store import DurableMarketEventStore
from app.market_data.models import MarketEvent,MarketEventType,TradePayload
NOW=datetime(2026,7,18,tzinfo=UTC)
def live_env(tmp_path):return {"TRADING_ENVIRONMENT":"LIVE","LIVE_TRADING_ENABLED":"true","WEBULL_ACCOUNT_ID":"acct","WEBULL_API_KEY":"key","WEBULL_API_SECRET":"secret","WEBULL_API_BASE_URL":"https://api.webull.com","WEBULL_STREAM_URL":"wss://events.webull.com","AUTHORIZATION_DATABASE_PATH":str(tmp_path/"a.db"),"EXECUTION_DATABASE_PATH":str(tmp_path/"e.db"),"MARKET_EVENT_DATABASE_PATH":str(tmp_path/"m.db"),"EMERGENCY_STOP_DATABASE_PATH":str(tmp_path/"s.db"),"MAX_ORDER_NOTIONAL":"10","MAX_DAILY_NOTIONAL":"50","MAX_OPEN_POSITIONS":"1","MAX_OPEN_ORDERS":"1","MAX_ORDER_RATE":"5","MAX_QUANTITY_PER_SYMBOL":"1","ALLOWED_SYMBOLS":"AAPL"}
def test_live_configuration_fails_closed(tmp_path):
 with pytest.raises(ValueError):load_configuration({"TRADING_ENVIRONMENT":"LIVE"})
 e=live_env(tmp_path);e["LIVE_TRADING_ENABLED"]="false"
 with pytest.raises(ValueError):load_configuration(e)
 assert load_configuration({}).environment is TradingEnvironment.TEST and not load_configuration({}).live_trading_enabled
def test_recursive_redaction():
 value=redact({"headers":{"Authorization":"Bearer secret","x-signature":"abc"},"items":[{"api_secret":"x"}],"url":"https://user:pass@example.com/x?token=x"})
 text=json.dumps(value);assert "Bearer secret" not in text and '"x"' not in text and "pass" not in text and "token=x" not in text
def test_live_rest_runtime_uses_official_sdk_without_custom_signer():
 from pathlib import Path
 source="\n".join((Path("app/webull")/name).read_text(encoding="utf-8").lower() for name in ("http_client.py","transport.py"))
 factory=Path("app/live_execution/broker_factory.py").read_text(encoding="utf-8").lower()
 assert "from webull.core.client import apiclient" in source
 assert "tradeclient" in source and "create_official_trade_client" in factory
 assert "webullrequestsigner" not in source+factory
 assert "hmac" not in source+factory
def test_durable_event_restart_duplicates_conflicts_and_corruption(tmp_path):
 path=tmp_path/"events.db";event=MarketEvent(1,NOW,"AAPL","feed",MarketEventType.TRADE,TradePayload(Decimal("10"),Decimal("1"),"t1"));store=DurableMarketEventStore(path)
 assert store.append(event,NOW) and not store.append(event,NOW);store.close();reopened=DurableMarketEventStore(path);assert reopened.replay().events==(event,)
 conflict=MarketEvent(1,NOW,"AAPL","feed",MarketEventType.TRADE,TradePayload(Decimal("11"),Decimal("1"),"t1"))
 with pytest.raises(ValueError,match="conflicting"):reopened.append(conflict,NOW)
 reopened.db.execute("UPDATE market_events SET payload_json='{}'")
 with pytest.raises(ValueError,match="corruption"):reopened.replay()
def test_emergency_stop_policy_survives_restart(tmp_path):
 path=tmp_path/"stop.db";store=EmergencyStopStore(path,lambda:NOW)
 with pytest.raises(ValueError):store.permit("SUBMIT")
 store.permit("CANCEL");store.clear("approved controlled validation");store.close();assert not EmergencyStopStore(path,lambda:NOW).state().enabled
def test_health_liveness_independent_and_readiness_fails_closed(tmp_path):
 env=live_env(tmp_path);env["TRADING_ENVIRONMENT"]="SANDBOX";config=load_configuration(env);inputs=ReadinessInputs(True,True,True,False,False,NOW,NOW,0,True)
 assert liveness()=={"status":"live"} and readiness(inputs,config,NOW)["status"]=="not_ready"
