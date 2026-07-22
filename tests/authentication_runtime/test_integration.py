import httpx
from app.authentication_runtime import AuthenticationRuntimePolicy,DeterministicAuthenticationRuntime
from app.authentication_transport import AuthenticationTransportPolicy,DeterministicAuthenticationTransportConnector
from app.composition import CompositionRoot,factory
from app.http_pipeline import DeterministicHTTPRequestPipeline,PipelinePolicy
from app.httpx_transport import HTTPXTransportAdapter,HTTPXTransportPolicy
from app.webull_authentication import WebullAuthenticationRequestFactory,WebullAuthenticationResponseVerifier
from tests.authentication_runtime.helpers import FakeProvider,request
from tests.authentication_transport.helpers import FakeAuthenticationService
from tests.webull_authentication.fixtures import SUCCESS,policy,profile
def graph():
 calls=[];client=httpx.Client(transport=httpx.MockTransport(lambda r:calls.append(r) or httpx.Response(200,headers={"x-synthetic-profile":"present"},json=SUCCESS)));root=CompositionRoot();root.register("provider",factory(FakeProvider));root.register("service",factory(FakeAuthenticationService));root.register("factory",factory(lambda:WebullAuthenticationRequestFactory(profile(),policy())));root.register("pipeline",factory(lambda:DeterministicHTTPRequestPipeline(PipelinePolicy())));root.register("client",factory(lambda:client));root.register("transport",factory(lambda c:HTTPXTransportAdapter(c,HTTPXTransportPolicy(enabled=True)),("client",)));root.register("verifier",factory(lambda:WebullAuthenticationResponseVerifier(profile(),policy())));root.register("connector",factory(lambda s,f,p,t,v:DeterministicAuthenticationTransportConnector(s,f,p,t,v,AuthenticationTransportPolicy(enabled=True)),("service","factory","pipeline","transport","verifier")));root.register("runtime",factory(lambda p,c:DeterministicAuthenticationRuntime(p,c,AuthenticationRuntimePolicy(enabled=True)),("provider","connector")));return root,client,calls
def test_construction_inert_execution_once_and_deterministic():
 root,client,calls=graph()
 try:
  container=root.build();provider=container.resolve("provider");assert calls==provider.calls==[];result=container.resolve("runtime").authenticate(request());assert result.success;assert len(calls)==len(provider.calls)==1
 finally:client.close()
