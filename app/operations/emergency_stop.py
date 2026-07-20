import sqlite3
from dataclasses import dataclass
from datetime import datetime
@dataclass(frozen=True,slots=True)
class EmergencyStopState:enabled:bool;reason:str;updated_at:datetime
class EmergencyStopStore:
 def __init__(self,path,clock):
  self.clock=clock;self.db=sqlite3.connect(str(path),isolation_level=None,check_same_thread=False);self.db.execute("CREATE TABLE IF NOT EXISTS emergency_stop(id INTEGER PRIMARY KEY CHECK(id=1),enabled INTEGER NOT NULL,reason TEXT NOT NULL,updated_at TEXT NOT NULL,schema_version INTEGER NOT NULL)")
  if not self.db.execute("SELECT 1 FROM emergency_stop WHERE id=1").fetchone():self.db.execute("INSERT INTO emergency_stop VALUES(1,1,'startup default',?,1)",(clock().isoformat(),))
 def state(self):
  e,r,t=self.db.execute("SELECT enabled,reason,updated_at FROM emergency_stop WHERE id=1").fetchone();return EmergencyStopState(bool(e),r,datetime.fromisoformat(t))
 def activate(self,reason):self.db.execute("UPDATE emergency_stop SET enabled=1,reason=?,updated_at=? WHERE id=1",(reason,self.clock().isoformat()));return self.state()
 def clear(self,reason):self.db.execute("UPDATE emergency_stop SET enabled=0,reason=?,updated_at=? WHERE id=1",(reason,self.clock().isoformat()));return self.state()
 def permit(self,operation):
  if self.state().enabled and operation in ("SUBMIT","REPLACE"):raise ValueError("emergency stop blocks live mutation")
 def reachable(self):self.db.execute("SELECT 1");return True
 def close(self):self.db.close()
