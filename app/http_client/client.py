from typing import Protocol
from app.http_client.exceptions import HTTPClientValidationError,HTTPParsingError,HTTPTransportError
from app.http_client.models import SerializedHTTPRequest,SerializedHTTPResponse
from app.http_client.parsers import HTTPResponseParser
from app.http_client.policies import HTTPClientPolicy
from app.http_client.serializers import HTTPRequestSerializer
from app.http_runtime import HTTPRequest
class HTTPTransportInterface(Protocol):
 def send(self,serialized_request:SerializedHTTPRequest)->SerializedHTTPResponse:...
class HTTPClient:
 def __init__(self,transport,policy:HTTPClientPolicy):
  if not hasattr(transport,"send"):raise HTTPClientValidationError("transport must implement send")
  if not isinstance(policy,HTTPClientPolicy):raise HTTPClientValidationError("policy must be HTTPClientPolicy")
  if policy.retries_enabled or policy.redirects_enabled or policy.cookies_enabled or policy.compression_enabled:raise HTTPClientValidationError("optional client behaviors are not implemented")
  self.transport=transport;self.policy=policy;self.serializer=HTTPRequestSerializer();self.parser=HTTPResponseParser()
 def execute(self,request):
  if not isinstance(request,HTTPRequest):raise HTTPClientValidationError("request must be HTTPRequest")
  if not self.policy.client_enabled:raise HTTPClientValidationError("HTTP client is disabled")
  serialized=self.serializer.serialize(request)
  try:raw=self.transport.send(serialized)
  except Exception as exc:raise HTTPTransportError("HTTP transport failed") from exc
  response=self.parser.parse(raw,request.correlation_id)
  if response.timestamp<request.timestamp:raise HTTPParsingError("response timestamp precedes request")
  return response
