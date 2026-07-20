import os
def integration_mode(environment=None):
 env=os.environ if environment is None else environment;mode=env.get("BROKER_INTEGRATION_MODE","").upper()
 if mode not in ("SANDBOX","PAPER","LIVE_READ_ONLY",""):raise ValueError("unsupported broker integration mode")
 if mode and not env.get("WEBULL_API_KEY"):raise ValueError("integration credentials are required")
 return mode or None
def live_mutations_allowed(environment=None):
 env=os.environ if environment is None else environment
 return env.get("BROKER_INTEGRATION_MODE","").upper()!="LIVE_READ_ONLY" or env.get("ALLOW_LIVE_INTEGRATION_MUTATIONS","").lower()=="true"
