from typing import Protocol
from app.http_runtime.models import HTTPRequest,HTTPResponse
class HTTPExecutor(Protocol):
 def execute(self,request:HTTPRequest)->HTTPResponse:...
