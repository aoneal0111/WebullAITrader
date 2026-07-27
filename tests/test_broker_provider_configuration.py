from app.configuration.loader import load_configuration


def test_default_provider_is_webull():
    configuration = load_configuration({})

    assert configuration.broker_provider == "webull"


def test_provider_is_case_normalized():
    configuration = load_configuration(
        {
            "BROKER_PROVIDER": "WEBULL",
        }
    )

    assert configuration.broker_provider == "webull"
