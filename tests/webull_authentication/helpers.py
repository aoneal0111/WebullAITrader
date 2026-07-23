from app.authentication import AuthenticationRequest
from app.authentication_transport import AuthenticationTransportContext,AuthenticationTransportRequest
from app.http_pipeline import HTTPResponseOperation,PipelineContext
def auth_request(**metadata):
 data={"attempt_id":"attempt-1","correlation_id":"correlation-1"};data.update(metadata)
 return AuthenticationRequest("broker","sign-in",("username_ref","password_ref","device_ref"),data)
def connector_request():return AuthenticationTransportRequest("attempt-1",auth_request(),AuthenticationTransportContext("correlation-1"))
def response(body,status=200,headers=(("x-synthetic-profile","present"),)):
 return HTTPResponseOperation("attempt-1:webull-auth-request:response",status,headers,body,PipelineContext("correlation-1"))
