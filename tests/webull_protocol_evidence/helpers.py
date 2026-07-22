class FakeProfileApprovalBoundary:
 def __init__(self):self.calls=[]
 def consider(self,assessment):self.calls.append(assessment);return assessment.eligible_for_profile_use
