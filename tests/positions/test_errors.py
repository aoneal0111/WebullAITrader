from app.positions import *
def test_hierarchy():assert issubclass(PositionsValidationError,PositionsError) and issubclass(PositionsDependencyError,PositionsError) and issubclass(PositionsSerializationError,PositionsError)
