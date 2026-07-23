class FakeApprovedProfileConsumer:
 def __init__(self):self.calls=[]
 def consume(self,result):
  self.calls.append(result)
  if not result.approved:raise ValueError("approval required")
  return result.approved_profile,result.approved_policy
