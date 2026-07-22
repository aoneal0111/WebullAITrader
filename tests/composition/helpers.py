class FakeTransport:
    def send(self, request):
        raise AssertionError("composition must not execute components")


class FakeGateway:
    def place_order(self, command):
        raise AssertionError("composition must not execute components")


class GatewayProtocolShell:
    def __init__(self, runtime):
        self.runtime = runtime

    def authenticate(self, request): pass
    def logout(self, request): pass
    def get_account(self, request): pass
    def submit_order(self, request): pass
    def cancel_order(self, request): pass
    def get_order_status(self, request): pass


class TransportExecutorShell:
    def __init__(self, http_runtime):
        self.http_runtime = http_runtime

    def execute(self, request):
        raise AssertionError("composition must not execute components")
