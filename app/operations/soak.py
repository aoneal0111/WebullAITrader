from dataclasses import asdict,dataclass
from datetime import datetime
from decimal import Decimal
import json
@dataclass(frozen=True,slots=True)
class SoakReport:
 start_time:datetime;end_time:datetime;duration:Decimal;orders_attempted:int;orders_accepted:int;orders_rejected:int;duplicate_orders:int;unresolved_mutations:int;reconciliation_failures:int;stream_disconnects:int;stream_reconnects:int;peak_memory:int;memory_growth:int;average_latency:Decimal;p95_latency:Decimal;p99_latency:Decimal;fatal_errors:tuple[str,...];final_result:str
 def to_json(self):
  value=asdict(self)
  for k,v in tuple(value.items()):
   if isinstance(v,datetime):value[k]=v.isoformat()
   elif isinstance(v,Decimal):value[k]=str(v)
  return json.dumps(value,sort_keys=True,separators=(",",":"))
def evaluate_soak(report,memory_growth_limit):
 failures=(report.duplicate_orders,report.reconciliation_failures,report.memory_growth>memory_growth_limit,report.fatal_errors)
 return "FAIL" if any(failures) else "PASS"
