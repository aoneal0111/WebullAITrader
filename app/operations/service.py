from __future__ import annotations
from dataclasses import dataclass,replace
from datetime import datetime
from threading import Event,Thread
@dataclass(frozen=True,slots=True)
class ServiceState:ready:bool;accepting_submissions:bool;started_at:datetime|None;last_reconciliation:datetime|None;failure:str|None=None
class OperationalService:
 def __init__(self,config,authorization_registry,execution_journal,market_store,emergency_stop,broker,stream,clock,reconcile,logger):
  self.config=config;self.authorization_registry=authorization_registry;self.execution_journal=execution_journal;self.market_store=market_store;self.emergency_stop=emergency_stop;self.broker=broker;self.stream=stream;self.clock=clock;self.reconcile=reconcile;self.logger=logger;self.state=ServiceState(False,False,None,None);self._stop=Event();self._thread=None
 def start(self):
  self.state=ServiceState(False,False,self.clock(),None)
  try:
   self.authorization_registry.authorizations;self.execution_journal.pending;self.market_store.reachable();self.emergency_stop.reachable();self.broker.connect();self.broker.get_account();self.reconcile();now=self.clock();self.stream.connect()
   if self.emergency_stop.state().enabled:raise ValueError("emergency stop is active")
   self.state=ServiceState(True,True,self.state.started_at,now);self._thread=Thread(target=self._loop,daemon=True);self._thread.start();self.logger.log("application_started","succeeded")
  except Exception as exc:self.state=replace(self.state,ready=False,accepting_submissions=False,failure=type(exc).__name__);self.logger.log("readiness_failed","failed",error_type=type(exc).__name__);raise
  return self.state
 def _loop(self):
  while not self._stop.wait(self.config.reconciliation_interval_seconds):
   try:self.reconcile();self.state=replace(self.state,last_reconciliation=self.clock())
   except Exception as exc:self.state=replace(self.state,ready=False,accepting_submissions=False,failure=type(exc).__name__)
 def shutdown(self,timeout_seconds=30):
  self.state=replace(self.state,ready=False,accepting_submissions=False);self._stop.set()
  if self._thread:self._thread.join(timeout_seconds)
  self.stream.disconnect();self.broker.disconnect();self.market_store.close();self.emergency_stop.close();self.authorization_registry.close();self.logger.log("application_stopped","succeeded")
