import inspect
from app.http_client import HTTPClient,HTTPClientPolicy
def test_no_production_capabilities():
 assert not HTTPClientPolicy().client_enabled;source=inspect.getsource(HTTPClient)
 for x in ("sleep(","Thread(","datetime.now","uuid","random","login","session"):
  assert x not in source
