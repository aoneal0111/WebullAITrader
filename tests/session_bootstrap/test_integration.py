from app.composition import CompositionRoot,factory
from app.session_bootstrap import DeterministicSessionBootstrapRuntime,SessionBootstrapPolicy
from tests.session_bootstrap.fixtures import approved_profile,request
from tests.session_bootstrap.helpers import FakeAuthenticationRuntime,FakeProvider,FakeSessionManager
def test_composition_construction_inert_then_exactly_one_each():
 p,a,s=FakeProvider(),FakeAuthenticationRuntime(),FakeSessionManager();root=CompositionRoot();root.register("approval",factory(approved_profile));root.register("provider",factory(lambda:p));root.register("authentication_runtime",factory(lambda:a));root.register("session_manager",factory(lambda:s));root.register("bootstrap",factory(lambda approval,provider,authentication,manager:DeterministicSessionBootstrapRuntime(approval,provider,authentication,manager,SessionBootstrapPolicy(enabled=True)),("approval","provider","authentication_runtime","session_manager")));runtime=root.build().resolve("bootstrap");assert p.calls==a.calls==s.calls==[];assert runtime.bootstrap(request()).success;assert len(p.calls)==len(a.calls)==len(s.calls)==1
