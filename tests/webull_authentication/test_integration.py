import httpx,pytest
from app.authentication import AuthenticationPolicy,AuthenticationStatus,DeterministicAuthenticationService
from app.authentication_transport import AuthenticationTransportPolicy,DeterministicAuthenticationTransportConnector
from app.composition import CompositionRoot,factory
from app.http_pipeline import DeterministicHTTPRequestPipeline,PipelinePolicy
from app.httpx_transport import HTTPXTransportAdapter,HTTPXTransportPolicy
from app.webull_authentication import WebullAuthenticationRequestFactory,WebullAuthenticationResponseVerifier
from tests.authentication.helpers import FakeCredentialProvider,FakeVerifier
from tests.webull_authentication.fixtures import MALFORMED,REJECTED,SUCCESS,policy,profile
from tests.webull_authentication.helpers import connector_request
def graph(body):
 calls=[];client=httpx.Client(transport=httpx.MockTransport(lambda request:calls.append(request) or httpx.Response(200,headers={"x-synthetic-profile":"present"},json=body)))
 root=CompositionRoot();root.register("auth",factory(lambda:DeterministicAuthenticationService(FakeCredentialProvider(),FakeVerifier(),AuthenticationPolicy())));root.register("factory",factory(lambda:WebullAuthenticationRequestFactory(profile(),policy())));root.register("pipeline",factory(lambda:DeterministicHTTPRequestPipeline(PipelinePolicy())));root.register("client",factory(lambda:client));root.register("transport",factory(lambda c:HTTPXTransportAdapter(c,HTTPXTransportPolicy(enabled=True)),("client",)));root.register("verifier",factory(lambda:WebullAuthenticationResponseVerifier(profile(),policy())));root.register("connector",factory(lambda a,f,p,t,v:DeterministicAuthenticationTransportConnector(a,f,p,t,v,AuthenticationTransportPolicy(enabled=True)),("auth","factory","pipeline","transport","verifier")));return root,client,calls
def test_construction_only_and_success_mock_transport():
 root,client,calls=graph(SUCCESS)
 try:
  container=root.build();assert calls==[];result=container.resolve("connector").authenticate(connector_request());assert result.success;assert len(calls)==1
 finally:client.close()
@pytest.mark.parametrize("body,error",[(REJECTED,False),(MALFORMED,True)])
def test_rejection_and_malformed_leave_authentication_unauthenticated(body,error):
 root,client,calls=graph(body)
 try:
  container=root.build()
  if error:
   with pytest.raises(Exception):container.resolve("connector").authenticate(connector_request())
  else:assert not container.resolve("connector").authenticate(connector_request()).success
  assert container.resolve("auth").state().status is AuthenticationStatus.UNAUTHENTICATED;assert len(calls)==1
 finally:client.close()
