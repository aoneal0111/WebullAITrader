from app.webull_authentication_approval import *
from tests.webull_authentication_approval.fixtures import configured
def test_material_mapping_explicit_actual_and_device_conditional():
 base=required_material_fields(configured());assert base==BASE_MATERIAL_PROFILE_FIELDS;assert set(base)<set(ALL_MATERIAL_PROFILE_FIELDS)
 device=required_material_fields(configured(include_device_identifier=True));assert device==BASE_MATERIAL_PROFILE_FIELDS+DEVICE_MATERIAL_PROFILE_FIELDS
