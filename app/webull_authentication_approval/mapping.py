BASE_MATERIAL_PROFILE_FIELDS=("endpoint_url","http_method","static_headers","username_field","password_field","username_reference","password_reference","success_field_path","success_values","failure_message_field_path","required_response_headers","verification_output_field_paths")
DEVICE_MATERIAL_PROFILE_FIELDS=("device_id_field","device_reference")
ALL_MATERIAL_PROFILE_FIELDS=BASE_MATERIAL_PROFILE_FIELDS+DEVICE_MATERIAL_PROFILE_FIELDS
def required_material_fields(configuration_result):return BASE_MATERIAL_PROFILE_FIELDS+(DEVICE_MATERIAL_PROFILE_FIELDS if configuration_result.policy.include_device_identifier else ())
