from app.http_client.exceptions import HTTPSerializationError
from app.http_client.models import SerializedHTTPRequest
from app.http_runtime import HTTPRequest
class HTTPRequestSerializer:
 def serialize(self,r):
  if not isinstance(r,HTTPRequest):raise HTTPSerializationError("request must be HTTPRequest")
  try:return SerializedHTTPRequest(r.request_id,r.method,r.url,r.headers,r.body,r.correlation_id,r.timestamp,{"deterministic":True})
  except ValueError as e:raise HTTPSerializationError("unable to serialize HTTP request") from e
