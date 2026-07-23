from app.session import SessionManager


def test_manager_protocol_operations_are_exact():
    public = {name for name in SessionManager.__dict__ if not name.startswith("_")}
    assert public == {"create", "activate", "invalidate", "replace", "state"}
