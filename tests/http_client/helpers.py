from datetime import timedelta
from app.http_client import *
from tests.http_runtime.helpers import request as http_request,STAMP
class FakeTransport:
 def __init__(self,response=None,fail=False):self.response=response;self.fail=fail;self.requests=[]
 def send(self,r):
  if not isinstance(r,SerializedHTTPRequest):raise TypeError("serialized request required")
  self.requests.append(r)
  if self.fail:raise ValueError("controlled")
  return self.response or SerializedHTTPResponse(200,{}, {"ok":True},r.correlation_id,r.timestamp+timedelta(seconds=1))
def policy(**x):
 v={"client_enabled":True};v.update(x);return HTTPClientPolicy(**v)
