from dataclasses import dataclass
from datetime import datetime
from app.operations.redaction import redact
@dataclass(frozen=True,slots=True)
class ReadinessInputs:
 authorization_database:bool;execution_database:bool;market_event_database:bool;broker_authenticated:bool;broker_connected:bool;reconciliation_timestamp:datetime|None;market_timestamp:datetime|None;unresolved_mutations:int;live_configuration_valid:bool
def liveness():return {"status":"live"}
def readiness(value,config,now):
 checks={"authorization_database":value.authorization_database,"execution_database":value.execution_database,"market_event_database":value.market_event_database,"broker_authenticated":value.broker_authenticated,"broker_connected":value.broker_connected,"live_configuration":value.live_configuration_valid,"unresolved_mutations":value.unresolved_mutations<=config.maximum_unresolved_mutations,"reconciliation_fresh":value.reconciliation_timestamp is not None and (now-value.reconciliation_timestamp).total_seconds()<=config.maximum_reconciliation_age_seconds,"market_fresh":value.market_timestamp is not None and (now-value.market_timestamp).total_seconds()<=config.maximum_market_data_age_seconds}
 return redact({"status":"ready" if all(checks.values()) else "not_ready","checks":checks})
