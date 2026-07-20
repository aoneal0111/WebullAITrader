import json
from dataclasses import dataclass
from datetime import datetime,timezone
from app.operations.redaction import redact
@dataclass(frozen=True,slots=True)
class JsonOperationalLogger:
 sink:object;environment:str;clock:object=lambda:datetime.now(timezone.utc)
 def log(self,event_type,result,severity="INFO",**fields):
  record={"timestamp":self.clock().isoformat(),"severity":severity,"event_type":event_type,"environment":self.environment,"result":result};record.update(fields);self.sink.emit(json.dumps(redact(record),sort_keys=True,separators=(",",":")))
