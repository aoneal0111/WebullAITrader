from app.market_data.nbbo import NbboSource, detect_nbbo_capabilities


def test_installed_provider_without_explicit_nbbo_api_is_unsupported():
    class ExistingQuotesOnly:
        class market_data:
            get_quotes = staticmethod(lambda: None)
            get_event_depth = staticmethod(lambda: None)

    capabilities = detect_nbbo_capabilities(ExistingQuotesOnly())
    assert not capabilities.programmatic
    assert capabilities.source is NbboSource.UNKNOWN
    assert capabilities.reason == "NBBO_CAPABILITY_UNAVAILABLE"
    assert not capabilities.bid_venue
    assert not capabilities.ask_venue


def test_explicit_nbbo_api_is_detected_without_invoking_network():
    class Provider:
        class market_data:
            @staticmethod
            def get_consolidated_quote(*_args, **_kwargs):
                raise AssertionError("capability detection must not call provider")

    capabilities = detect_nbbo_capabilities(Provider())
    assert capabilities.programmatic
    assert capabilities.source is NbboSource.REST
    assert capabilities.reason == "EXPLICIT_NBBO_API_PRESENT"


def test_missing_client_is_explicitly_unavailable():
    capabilities = detect_nbbo_capabilities(None)
    assert not capabilities.programmatic
    assert capabilities.reason == "NBBO_CLIENT_UNAVAILABLE"
