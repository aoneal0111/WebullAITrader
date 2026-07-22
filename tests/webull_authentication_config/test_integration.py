import httpx,pytest
from app.authentication_runtime import AuthenticationRuntimePolicy,DeterministicAuthenticationRuntime
from app.authentication_transport import AuthenticationTransportPolicy,DeterministicAuthenticationTransportConnector
from app.composition import CompositionRoot,factory
from app.http_pipeline import DeterministicHTTPRequestPipeline,PipelinePolicy
from app.httpx_transport import HTTPXTransportAdapter,HTTPXTransportPolicy
from app.webull_authentication import WebullAuthenticationDisabledError,WebullAuthenticationRequestFactory,WebullAuthenticationResponseVerifier
from app.webull_authentication_config import DeterministicWebullAuthenticationProfileLoader,WebullAuthenticationConfigurationProfileError
from tests.authentication_runtime.helpers import FakeProvider
from tests.authentication_transport.helpers import FakeAuthenticationService
from tests.authentication_runtime.helpers import request as runtime_request
from tests.webull_authentication.fixtures import SUCCESS
from tests.webull_authentication_config.fixtures import configuration
def graph(config):
 calls=[];loaded=DeterministicWebullAuthenticationProfileLoader().load(config);client=httpx.Client(transport=httpx.MockTransport(lambda r:calls.append(r) or httpx.Response(200,headers={"x-synthetic-profile":"present"},json=SUCCESS)));root=CompositionRoot();root.register("provider",factory(FakeProvider));root.register("service",factory(FakeAuthenticationService));root.register("request_factory",factory(lambda:WebullAuthenticationRequestFactory(loaded.profile,loaded.policy)));root.register("pipeline",factory(lambda:DeterministicHTTPRequestPipeline(PipelinePolicy())));root.register("client",factory(lambda:client));root.register("transport",factory(lambda c:HTTPXTransportAdapter(c,HTTPXTransportPolicy(enabled=True)),("client",)));root.register("verifier",factory(lambda:WebullAuthenticationResponseVerifier(loaded.profile,loaded.policy)));root.register("connector",factory(lambda s,f,p,t,v:DeterministicAuthenticationTransportConnector(s,f,p,t,v,AuthenticationTransportPolicy(enabled=True)),("service","request_factory","pipeline","transport","verifier")));root.register("runtime",factory(lambda p,c:DeterministicAuthenticationRuntime(p,c,AuthenticationRuntimePolicy(enabled=True)),("provider","connector")));return root,client,calls
def test_construction_inert_and_loaded_end_to_end_once():
 root,client,calls=graph(configuration())
 try:
  container=root.build();assert calls==[];assert container.resolve("runtime").authenticate(runtime_request()).success;assert len(calls)==1
 finally:client.close()
def test_disabled_loaded_policy_blocks_without_transport():
 root,client,calls=graph(configuration(enabled=False))
 try:
  with pytest.raises(Exception):root.build().resolve("runtime").authenticate(runtime_request())
  assert calls==[]
 finally:client.close()
def test_invalid_configuration_prevents_graph_construction():
 with pytest.raises(WebullAuthenticationConfigurationProfileError):graph(configuration(endpoint_url="invalid"))
