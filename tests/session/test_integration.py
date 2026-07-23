from app.composition import CompositionRoot, factory, implements_methods
from app.session import DeterministicSessionManager, SessionPolicy, SessionStatus
from tests.session.helpers import FakeAuthenticationService, request


def test_composition_constructs_fake_session_graph_and_transitions_only():
    auth = FakeAuthenticationService()
    root = CompositionRoot()
    root.register("authentication_service", factory(
        lambda: auth, validator=implements_methods("authenticate", "logout", "state")))
    root.register("session_manager", factory(
        lambda dependency: DeterministicSessionManager(dependency, SessionPolicy()),
        ("authentication_service",),
        implements_methods("create", "activate", "invalidate", "replace", "state"),
    ))
    manager = root.build().resolve("session_manager")
    assert auth.state_calls == 0
    manager.create(request())
    manager.activate()
    assert manager.state().status is SessionStatus.ACTIVE
    assert auth.authentication_calls == 0
