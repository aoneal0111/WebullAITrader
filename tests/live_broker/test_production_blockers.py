from app.live_broker import *
from tests.live_broker.helpers import request
def test_default_policy_blocks_production_execution():
 r=request(policy=LiveExecutionPolicy());i=LiveExecutionGuard().authorize(r);assert i.decision is LiveExecutionDecision.BLOCKED and i.reason is LiveExecutionReason.LIVE_POLICY_DISABLED
