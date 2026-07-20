from __future__ import annotations
import hashlib,json,sqlite3
from datetime import datetime
from pathlib import Path
from app.market_data.models import MarketEvent,MarketEventLog
from app.market_data.recorder import _safe,event_log_from_json
class DurableMarketEventStore:
 def __init__(self,path):
  self.path=str(Path(path).resolve());self.db=sqlite3.connect(self.path,timeout=30,isolation_level=None,check_same_thread=False);self.db.execute("PRAGMA journal_mode=WAL");self.db.execute("PRAGMA synchronous=FULL")
  self.db.executescript("CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);CREATE TABLE IF NOT EXISTS market_events(event_id TEXT PRIMARY KEY,source TEXT NOT NULL,sequence INTEGER NOT NULL,event_type TEXT NOT NULL,event_timestamp TEXT NOT NULL,received_timestamp TEXT NOT NULL,payload_json TEXT NOT NULL,payload_digest TEXT NOT NULL,schema_version INTEGER NOT NULL,UNIQUE(source,sequence));CREATE INDEX IF NOT EXISTS ix_market_source_sequence ON market_events(source,sequence);CREATE INDEX IF NOT EXISTS ix_market_timestamp ON market_events(event_timestamp);")
  row=self.db.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
  if row is None:self.db.execute("INSERT INTO metadata VALUES('schema_version','1')")
  elif row[0]!="1":raise ValueError("unsupported market event schema")
 def append(self,event:MarketEvent,received_timestamp:datetime):
  if received_timestamp.tzinfo is None:raise ValueError("received timestamp must be timezone-aware")
  payload=json.dumps(_safe(event),sort_keys=True,separators=(",",":"));digest=hashlib.sha256(payload.encode()).hexdigest();identity=f"{event.source}:{event.sequence}"
  self.db.execute("BEGIN IMMEDIATE")
  try:
   row=self.db.execute("SELECT payload_digest FROM market_events WHERE event_id=?",(identity,)).fetchone()
   if row:
    if row[0]!=digest:raise ValueError("conflicting duplicate market event")
    self.db.execute("COMMIT");return False
   self.db.execute("INSERT INTO market_events VALUES(?,?,?,?,?,?,?,?,?)",(identity,event.source,event.sequence,event.event_type.value,event.timestamp.isoformat(),received_timestamp.isoformat(),payload,digest,1));self.db.execute("COMMIT");return True
  except Exception:
   if self.db.in_transaction:self.db.execute("ROLLBACK")
   raise
 def replay(self,*,after_event_id=None):
  rows=self.db.execute("SELECT event_id,payload_json,payload_digest FROM market_events ORDER BY event_timestamp,source,sequence").fetchall();events=[];started=after_event_id is None
  for identity,payload,digest in rows:
   if hashlib.sha256(payload.encode()).hexdigest()!=digest:raise ValueError("market event corruption detected")
   if not started:
    started=identity==after_event_id;continue
   raw=json.loads(payload);events.extend(event_log_from_json(json.dumps({"schema_version":1,"events":[raw]})).events)
  return MarketEventLog(tuple(events))
 def reachable(self):self.db.execute("SELECT 1").fetchone();return True
 def close(self):self.db.close()
