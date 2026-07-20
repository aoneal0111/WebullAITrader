from app.redaction import redact_account_number


def test_redacts_all_but_final_four() -> None:
    assert redact_account_number("123456789") == "****6789"


def test_short_and_empty_values_are_safe() -> None:
    assert redact_account_number("12") == "****12"
    assert redact_account_number("") == "****"
