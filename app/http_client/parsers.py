from app.http_client.exceptions import HTTPParsingError
from app.http_client.models import SerializedHTTPResponse
from app.http_runtime import HTTPResponse
class HTTPResponseParser:
 def parse(self,r,expected_correlation_id):
  if not isinstance(r,SerializedHTTPResponse):raise HTTPParsingError("response must be SerializedHTTPResponse")
  if r.correlation_id!=expected_correlation_id:raise HTTPParsingError("response correlation mismatch")
  try:return HTTPResponse(status_code=r.status_code,headers=r.headers,body=r.body,correlation_id=r.correlation_id,timestamp=r.timestamp,metadata=r.metadata)
  except ValueError as e:raise HTTPParsingError("unable to parse HTTP response") from e
