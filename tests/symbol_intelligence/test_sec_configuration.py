from __future__ import annotations

import json

import pytest

from app.configuration import SECEdgarConfiguration, load_configuration
from app.configuration.environment import (
    SYMBOL_INTELLIGENCE_SEC_CANONICAL_KEYS,
    parse_symbol_intelligence_sec_manual_targets,
    resolve_symbol_intelligence_sec_environment,
)
from app.configuration.loader import load_symbol_intelligence_sec_configuration


CANONICAL_AGENT = "canonical agent contact@example.invalid"
LEGACY_AGENT = "legacy agent contact@example.invalid"
REPR_SENTINEL = "FAKE-SEC-USER-AGENT-REPR-SENTINEL"


def _write_dotenv(tmp_path, values: dict[str, str]):
    path = tmp_path / ".env"
    path.write_text(
        "".join(f"{key}={value}\n" for key, value in values.items()),
        encoding="utf-8",
    )
    return path


def _resolve(process=None, *, dotenv_path=None):
    return resolve_symbol_intelligence_sec_environment(
        {} if process is None else process,
        dotenv_path=dotenv_path,
    )


def _configuration(process=None, *, dotenv_path=None):
    return load_symbol_intelligence_sec_configuration(
        _resolve(process, dotenv_path=dotenv_path)
    )


def test_disabled_with_no_user_agent_uses_bounded_defaults() -> None:
    config = _configuration()
    assert config.enabled is False
    assert config.user_agent is None
    assert config.requests_per_second == 2.0
    assert config.connect_timeout_seconds == 3.0
    assert config.read_timeout_seconds == 10.0
    assert config.max_retries == 2
    assert config.max_ticker_entries == 25_000
    assert config.max_submissions_cache_entries == 2_048
    assert config.shadow_parity_enabled is False
    assert config.acquisition_enabled is False
    assert config.dual_network_migration_enabled is False


def test_manual_targets_are_absent_by_default() -> None:
    assert parse_symbol_intelligence_sec_manual_targets({}) == ()
    assert _configuration().manual_targets == ()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("AAPL", ("AAPL",)),
        ("AAPL,MSFT", ("AAPL", "MSFT")),
        (" AAPL , MSFT , NVDA ", ("AAPL", "MSFT", "NVDA")),
        ("AAPL,AAPL", ("AAPL", "AAPL")),
        ("?BAD", ("?BAD",)),
    ],
)
def test_manual_targets_parse_structure_without_symbol_validation(raw, expected) -> None:
    assert parse_symbol_intelligence_sec_manual_targets(
        {"ATLAS_SYMBOL_INTELLIGENCE_SEC_MANUAL_TARGETS": raw}
    ) == expected


@pytest.mark.parametrize("raw", ["", " ", ",", "AAPL,", ",AAPL", "AAPL,,MSFT", "AAPL, ,MSFT"])
def test_manual_targets_reject_empty_values_and_tokens(raw) -> None:
    with pytest.raises(ValueError):
        parse_symbol_intelligence_sec_manual_targets(
            {"ATLAS_SYMBOL_INTELLIGENCE_SEC_MANUAL_TARGETS": raw}
        )


def test_manual_targets_reject_over_limit_without_truncation() -> None:
    with pytest.raises(ValueError):
        parse_symbol_intelligence_sec_manual_targets(
            {"ATLAS_SYMBOL_INTELLIGENCE_SEC_MANUAL_TARGETS": "A,B,C,D"}
        )


def test_manual_targets_ignore_dotenv_and_consume_process_value(tmp_path) -> None:
    dotenv = _write_dotenv(
        tmp_path,
        {"ATLAS_SYMBOL_INTELLIGENCE_SEC_MANUAL_TARGETS": "AAPL"},
    )
    assert parse_symbol_intelligence_sec_manual_targets({}) == ()
    assert parse_symbol_intelligence_sec_manual_targets(
        {"ATLAS_SYMBOL_INTELLIGENCE_SEC_MANUAL_TARGETS": " MSFT "}
    ) == ("MSFT",)
    resolved = resolve_symbol_intelligence_sec_environment({}, dotenv_path=dotenv)
    assert resolved.get("ATLAS_SYMBOL_INTELLIGENCE_SEC_MANUAL_TARGETS") is None


def test_manual_targets_flow_into_immutable_sec_configuration_without_flags() -> None:
    config = load_configuration(
        {
            "ATLAS_SYMBOL_INTELLIGENCE_SEC_MANUAL_TARGETS": " AAPL, MSFT ",
        }
    ).symbol_intelligence_sec_edgar
    assert config.manual_targets == ("AAPL", "MSFT")
    assert config.acquisition_enabled is False
    assert config.shadow_parity_enabled is False
    assert config.dual_network_migration_enabled is False
    with pytest.raises(AttributeError):
        config.manual_targets += ("NVDA",)


def test_all_canonical_names_populate_the_dedicated_contract() -> None:
    config = _configuration({
        "ATLAS_SEC_EDGAR_ENABLED": "true",
        "ATLAS_SEC_EDGAR_USER_AGENT": CANONICAL_AGENT,
        "ATLAS_SEC_EDGAR_REQUESTS_PER_SECOND": "1.5",
        "ATLAS_SEC_EDGAR_CONNECT_TIMEOUT_SECONDS": "2.5",
        "ATLAS_SEC_EDGAR_READ_TIMEOUT_SECONDS": "7.5",
        "ATLAS_SEC_EDGAR_MAX_RETRIES": "3",
        "ATLAS_SEC_EDGAR_BACKOFF_INITIAL_SECONDS": "0.25",
        "ATLAS_SEC_EDGAR_BACKOFF_MAX_SECONDS": "4",
        "ATLAS_SEC_EDGAR_FAILURE_COOLDOWN_SECONDS": "45",
        "ATLAS_SEC_EDGAR_FRESHNESS_DAYS": "5",
        "ATLAS_SEC_EDGAR_TICKER_REFRESH_SECONDS": "7200",
        "ATLAS_SEC_EDGAR_SUBMISSIONS_REFRESH_SECONDS": "300",
        "ATLAS_SEC_EDGAR_MAX_TICKER_ENTRIES": "30000",
        "ATLAS_SEC_EDGAR_MAX_SUBMISSIONS_CACHE_ENTRIES": "3000",
    })
    assert config.enabled is True
    assert config.user_agent is not None
    assert config.requests_per_second == 1.5
    assert config.connect_timeout_seconds == 2.5
    assert config.read_timeout_seconds == 7.5
    assert config.max_retries == 3
    assert config.backoff_initial_seconds == 0.25
    assert config.backoff_max_seconds == 4.0
    assert config.failure_cooldown_seconds == 45.0
    assert config.freshness_days == 5
    assert config.ticker_refresh_seconds == 7200.0
    assert config.submissions_refresh_seconds == 300.0
    assert config.max_ticker_entries == 30_000
    assert config.max_submissions_cache_entries == 3_000


def test_explicitly_disabled_with_user_agent_is_valid() -> None:
    config = _configuration({
        "ATLAS_SEC_EDGAR_ENABLED": "false",
        "ATLAS_SEC_EDGAR_USER_AGENT": CANONICAL_AGENT,
    })
    assert config.enabled is False
    assert config.diagnostic_summary() == {
        "enabled": False,
        "shadow_parity_enabled": False,
        "acquisition_enabled": False,
        "dual_network_migration_enabled": False,
        "user_agent_configured": True,
    }


@pytest.mark.parametrize("value, expected", [("true", True), ("false", False)])
def test_shadow_parity_flag_is_canonical_and_disabled_by_default(value, expected) -> None:
    config = _configuration({"ATLAS_SYMBOL_INTELLIGENCE_SEC_SHADOW_PARITY_ENABLED": value})
    assert config.shadow_parity_enabled is expected


def test_shadow_parity_flag_process_precedes_dotenv(tmp_path) -> None:
    resolved = _resolve(
        {"ATLAS_SYMBOL_INTELLIGENCE_SEC_SHADOW_PARITY_ENABLED": "false"},
        dotenv_path=_write_dotenv(
            tmp_path,
            {"ATLAS_SYMBOL_INTELLIGENCE_SEC_SHADOW_PARITY_ENABLED": "true"},
        ),
    )
    config = load_symbol_intelligence_sec_configuration(resolved)
    assert config.shadow_parity_enabled is False


def test_shadow_parity_flag_dotenv_only_true(tmp_path) -> None:
    config = _configuration(
        {},
        dotenv_path=_write_dotenv(
            tmp_path,
            {"ATLAS_SYMBOL_INTELLIGENCE_SEC_SHADOW_PARITY_ENABLED": "true"},
        ),
    )
    assert config.shadow_parity_enabled is True


def test_shadow_parity_flag_dotenv_only_false(tmp_path) -> None:
    config = _configuration(
        {},
        dotenv_path=_write_dotenv(
            tmp_path,
            {"ATLAS_SYMBOL_INTELLIGENCE_SEC_SHADOW_PARITY_ENABLED": "false"},
        ),
    )
    assert config.shadow_parity_enabled is False


def test_shadow_parity_flag_invalid_and_empty_values_fail() -> None:
    for value in ("enabled", ""):
        with pytest.raises(ValueError, match="boolean setting is malformed"):
            _configuration({"ATLAS_SYMBOL_INTELLIGENCE_SEC_SHADOW_PARITY_ENABLED": value})


@pytest.mark.parametrize(
    ("name", "field"),
    (
        ("ATLAS_SYMBOL_INTELLIGENCE_SEC_ACQUISITION_ENABLED", "acquisition_enabled"),
        (
            "ATLAS_SYMBOL_INTELLIGENCE_SEC_DUAL_NETWORK_MIGRATION_ENABLED",
            "dual_network_migration_enabled",
        ),
    ),
)
@pytest.mark.parametrize("value, expected", (("true", True), ("false", False)))
def test_acquisition_controls_load_from_canonical_process(name, field, value, expected) -> None:
    config = _configuration({name: value})
    assert getattr(config, field) is expected


@pytest.mark.parametrize(
    ("name", "field"),
    (
        ("ATLAS_SYMBOL_INTELLIGENCE_SEC_ACQUISITION_ENABLED", "acquisition_enabled"),
        (
            "ATLAS_SYMBOL_INTELLIGENCE_SEC_DUAL_NETWORK_MIGRATION_ENABLED",
            "dual_network_migration_enabled",
        ),
    ),
)
def test_acquisition_controls_process_precede_dotenv(name, field, tmp_path) -> None:
    resolved = _resolve(
        {name: "false"},
        dotenv_path=_write_dotenv(tmp_path, {name: "true"}),
    )
    config = load_symbol_intelligence_sec_configuration(resolved)
    assert getattr(config, field) is False
    assert resolved.origin(name) == "canonical_process"


@pytest.mark.parametrize(
    ("name", "field", "value", "expected"),
    (
        ("ATLAS_SYMBOL_INTELLIGENCE_SEC_ACQUISITION_ENABLED", "acquisition_enabled", "true", True),
        ("ATLAS_SYMBOL_INTELLIGENCE_SEC_ACQUISITION_ENABLED", "acquisition_enabled", "false", False),
        (
            "ATLAS_SYMBOL_INTELLIGENCE_SEC_DUAL_NETWORK_MIGRATION_ENABLED",
            "dual_network_migration_enabled",
            "true",
            True,
        ),
        (
            "ATLAS_SYMBOL_INTELLIGENCE_SEC_DUAL_NETWORK_MIGRATION_ENABLED",
            "dual_network_migration_enabled",
            "false",
            False,
        ),
    ),
)
def test_acquisition_controls_load_from_dotenv(name, field, value, expected, tmp_path) -> None:
    config = _configuration({}, dotenv_path=_write_dotenv(tmp_path, {name: value}))
    assert getattr(config, field) is expected


@pytest.mark.parametrize(
    "name",
    (
        "ATLAS_SYMBOL_INTELLIGENCE_SEC_ACQUISITION_ENABLED",
        "ATLAS_SYMBOL_INTELLIGENCE_SEC_DUAL_NETWORK_MIGRATION_ENABLED",
    ),
)
@pytest.mark.parametrize("value", ("", "enabled", "1", "yes"))
def test_acquisition_controls_reject_empty_and_invalid_values(name, value) -> None:
    with pytest.raises(ValueError, match="boolean setting is malformed"):
        _configuration({name: value})


def test_shadow_acquisition_and_migration_flags_are_independent() -> None:
    shadow = _configuration({"ATLAS_SYMBOL_INTELLIGENCE_SEC_SHADOW_PARITY_ENABLED": "true"})
    acquisition = _configuration({"ATLAS_SYMBOL_INTELLIGENCE_SEC_ACQUISITION_ENABLED": "true"})
    migration = _configuration({"ATLAS_SYMBOL_INTELLIGENCE_SEC_DUAL_NETWORK_MIGRATION_ENABLED": "true"})
    assert shadow.shadow_parity_enabled is True
    assert shadow.acquisition_enabled is False
    assert shadow.dual_network_migration_enabled is False
    assert acquisition.shadow_parity_enabled is False
    assert acquisition.acquisition_enabled is True
    assert acquisition.dual_network_migration_enabled is False
    assert migration.shadow_parity_enabled is False
    assert migration.acquisition_enabled is False
    assert migration.dual_network_migration_enabled is True


def test_new_acquisition_controls_have_no_legacy_aliases() -> None:
    config = _configuration({
        "SYMBOL_INTELLIGENCE_SEC_ACQUISITION_ENABLED": "true",
        "SYMBOL_INTELLIGENCE_SEC_DUAL_NETWORK_MIGRATION_ENABLED": "true",
    })
    assert config.acquisition_enabled is False
    assert config.dual_network_migration_enabled is False


def test_enabled_accepts_canonical_or_legacy_user_agent() -> None:
    canonical = _configuration({
        "ATLAS_SEC_EDGAR_ENABLED": "true",
        "ATLAS_SEC_EDGAR_USER_AGENT": CANONICAL_AGENT,
    })
    legacy = _configuration({
        "ATLAS_SEC_EDGAR_ENABLED": "true",
        "SEC_EDGAR_USER_AGENT": LEGACY_AGENT,
    })
    assert canonical.enabled and canonical.user_agent is not None
    assert legacy.enabled and legacy.user_agent is not None


def test_legacy_user_agent_alone_compatibly_enables_but_canonical_alone_does_not() -> None:
    legacy = _configuration({"SEC_EDGAR_USER_AGENT": LEGACY_AGENT})
    canonical = _configuration({"ATLAS_SEC_EDGAR_USER_AGENT": CANONICAL_AGENT})
    assert legacy.enabled is True
    assert canonical.enabled is False


@pytest.mark.parametrize(
    ("process", "dotenv", "expected_origin", "expected_enabled"),
    (
        (
            {
                "ATLAS_SEC_EDGAR_ENABLED": "true",
                "ATLAS_SEC_EDGAR_USER_AGENT": CANONICAL_AGENT,
            },
            {"SEC_EDGAR_USER_AGENT": LEGACY_AGENT},
            "canonical_process",
            True,
        ),
        (
            {"SEC_EDGAR_USER_AGENT": LEGACY_AGENT},
            {
                "ATLAS_SEC_EDGAR_ENABLED": "true",
                "ATLAS_SEC_EDGAR_USER_AGENT": CANONICAL_AGENT,
            },
            "legacy_process",
            True,
        ),
        (
            {
                "ATLAS_SEC_EDGAR_ENABLED": "true",
                "ATLAS_SEC_EDGAR_USER_AGENT": CANONICAL_AGENT,
                "SEC_EDGAR_USER_AGENT": LEGACY_AGENT,
            },
            {},
            "canonical_process",
            True,
        ),
        (
            {},
            {
                "ATLAS_SEC_EDGAR_ENABLED": "true",
                "ATLAS_SEC_EDGAR_USER_AGENT": CANONICAL_AGENT,
                "SEC_EDGAR_USER_AGENT": LEGACY_AGENT,
            },
            "canonical_dotenv",
            True,
        ),
    ),
)
def test_source_aware_user_agent_precedence(
    tmp_path,
    process,
    dotenv,
    expected_origin,
    expected_enabled,
) -> None:
    resolved = _resolve(process, dotenv_path=_write_dotenv(tmp_path, dotenv))
    config = load_symbol_intelligence_sec_configuration(resolved)
    assert resolved.origin("ATLAS_SEC_EDGAR_USER_AGENT") == expected_origin
    assert config.enabled is expected_enabled
    assert config.user_agent is not None


def test_explicit_process_false_wins_over_dotenv_true(tmp_path) -> None:
    resolved = _resolve(
        {
            "ATLAS_SEC_EDGAR_ENABLED": "false",
            "ATLAS_SEC_EDGAR_USER_AGENT": CANONICAL_AGENT,
        },
        dotenv_path=_write_dotenv(
            tmp_path,
            {"ATLAS_SEC_EDGAR_ENABLED": "true"},
        ),
    )
    config = load_symbol_intelligence_sec_configuration(resolved)
    assert resolved.origin("ATLAS_SEC_EDGAR_ENABLED") == "canonical_process"
    assert config.enabled is False


def test_default_loader_preserves_explicit_process_values_over_dotenv(
    tmp_path,
    monkeypatch,
) -> None:
    for name in SYMBOL_INTELLIGENCE_SEC_CANONICAL_KEYS + (
        "SEC_EDGAR_ENABLED",
        "SEC_EDGAR_USER_AGENT",
        "SEC_EDGAR_FRESHNESS_DAYS",
        "SEC_EDGAR_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ATLAS_SEC_EDGAR_ENABLED", "false")
    monkeypatch.setenv("ATLAS_SEC_EDGAR_USER_AGENT", "")
    _write_dotenv(
        tmp_path,
        {
            "ATLAS_SEC_EDGAR_ENABLED": "true",
            "ATLAS_SEC_EDGAR_USER_AGENT": CANONICAL_AGENT,
        },
    )
    monkeypatch.chdir(tmp_path)

    config = load_configuration().symbol_intelligence_sec_edgar

    assert config.enabled is False
    assert config.user_agent is None


def test_explicit_empty_process_user_agent_blocks_dotenv_fallback(tmp_path) -> None:
    resolved = _resolve(
        {
            "ATLAS_SEC_EDGAR_ENABLED": "true",
            "ATLAS_SEC_EDGAR_USER_AGENT": "",
        },
        dotenv_path=_write_dotenv(
            tmp_path,
            {"ATLAS_SEC_EDGAR_USER_AGENT": CANONICAL_AGENT},
        ),
    )
    assert resolved.origin("ATLAS_SEC_EDGAR_USER_AGENT") == "canonical_process"
    with pytest.raises(ValueError, match="required when enabled"):
        load_symbol_intelligence_sec_configuration(resolved)


def test_enabled_requires_user_agent_and_invalid_boolean_fails() -> None:
    with pytest.raises(ValueError, match="required when enabled"):
        _configuration({"ATLAS_SEC_EDGAR_ENABLED": "true"})
    with pytest.raises(ValueError, match="boolean setting is malformed"):
        _configuration({"ATLAS_SEC_EDGAR_ENABLED": "enabled"})


def test_legacy_timeout_and_freshness_aliases_feed_only_new_compatibility_contract() -> None:
    config = _configuration({
        "SEC_EDGAR_USER_AGENT": LEGACY_AGENT,
        "SEC_EDGAR_TIMEOUT_SECONDS": "4.5",
        "SEC_EDGAR_FRESHNESS_DAYS": "2",
    })
    assert config.connect_timeout_seconds == 4.5
    assert config.read_timeout_seconds == 4.5
    assert config.freshness_days == 2


@pytest.mark.parametrize(
    ("name", "value"),
    (
        ("ATLAS_SEC_EDGAR_CONNECT_TIMEOUT_SECONDS", "nan"),
        ("ATLAS_SEC_EDGAR_READ_TIMEOUT_SECONDS", "inf"),
        ("ATLAS_SEC_EDGAR_READ_TIMEOUT_SECONDS", "-inf"),
        ("ATLAS_SEC_EDGAR_CONNECT_TIMEOUT_SECONDS", "0"),
        ("ATLAS_SEC_EDGAR_CONNECT_TIMEOUT_SECONDS", ""),
        ("ATLAS_SEC_EDGAR_REQUESTS_PER_SECOND", "0"),
        ("ATLAS_SEC_EDGAR_REQUESTS_PER_SECOND", "-0"),
        ("ATLAS_SEC_EDGAR_REQUESTS_PER_SECOND", "true"),
        ("ATLAS_SEC_EDGAR_REQUESTS_PER_SECOND", "5.01"),
        ("ATLAS_SEC_EDGAR_MAX_RETRIES", "-1"),
        ("ATLAS_SEC_EDGAR_MAX_RETRIES", "1.5"),
        ("ATLAS_SEC_EDGAR_MAX_RETRIES", "5"),
        ("ATLAS_SEC_EDGAR_BACKOFF_INITIAL_SECONDS", "-0.1"),
        ("ATLAS_SEC_EDGAR_BACKOFF_MAX_SECONDS", "30.1"),
        ("ATLAS_SEC_EDGAR_FAILURE_COOLDOWN_SECONDS", "0"),
        ("ATLAS_SEC_EDGAR_FRESHNESS_DAYS", "0"),
        ("ATLAS_SEC_EDGAR_TICKER_REFRESH_SECONDS", "0"),
        ("ATLAS_SEC_EDGAR_SUBMISSIONS_REFRESH_SECONDS", "0"),
        ("ATLAS_SEC_EDGAR_MAX_TICKER_ENTRIES", "0"),
        ("ATLAS_SEC_EDGAR_MAX_TICKER_ENTRIES", "50001"),
        ("ATLAS_SEC_EDGAR_MAX_SUBMISSIONS_CACHE_ENTRIES", "0"),
        ("ATLAS_SEC_EDGAR_MAX_SUBMISSIONS_CACHE_ENTRIES", "8193"),
    ),
)
def test_invalid_numeric_bounds_fail_visibly(name: str, value: str) -> None:
    with pytest.raises((TypeError, ValueError)):
        _configuration({name: value})


def test_backoff_max_cannot_be_below_initial() -> None:
    with pytest.raises(ValueError, match="below initial"):
        _configuration({
            "ATLAS_SEC_EDGAR_BACKOFF_INITIAL_SECONDS": "2",
            "ATLAS_SEC_EDGAR_BACKOFF_MAX_SECONDS": "1",
        })


def test_numeric_whitespace_scientific_notation_and_negative_zero() -> None:
    config = _configuration({
        "ATLAS_SEC_EDGAR_REQUESTS_PER_SECOND": " 2e0 ",
        "ATLAS_SEC_EDGAR_CONNECT_TIMEOUT_SECONDS": " 4.5 ",
        "ATLAS_SEC_EDGAR_BACKOFF_INITIAL_SECONDS": "-0",
        "ATLAS_SEC_EDGAR_BACKOFF_MAX_SECONDS": "-0",
    })
    assert config.requests_per_second == 2.0
    assert config.connect_timeout_seconds == 4.5
    assert config.backoff_initial_seconds == 0.0
    assert config.backoff_max_seconds == 0.0


def test_user_agent_is_absent_from_repr_diagnostics_and_logs(caplog) -> None:
    resolved = _resolve({
        "ATLAS_SEC_EDGAR_ENABLED": "true",
        "ATLAS_SEC_EDGAR_USER_AGENT": CANONICAL_AGENT,
    })
    config = load_symbol_intelligence_sec_configuration(resolved)
    serialized_diagnostics = json.dumps(config.diagnostic_summary())
    assert CANONICAL_AGENT not in repr(resolved)
    assert CANONICAL_AGENT not in repr(config)
    assert CANONICAL_AGENT not in serialized_diagnostics
    assert CANONICAL_AGENT not in caplog.text


def test_legacy_and_operational_reprs_hide_user_agent_without_removing_value() -> None:
    legacy_value = SECEdgarConfiguration(user_agent=REPR_SENTINEL)
    canonical = load_configuration({
        "ATLAS_SEC_EDGAR_ENABLED": "true",
        "ATLAS_SEC_EDGAR_USER_AGENT": REPR_SENTINEL,
    })
    legacy = load_configuration({"SEC_EDGAR_USER_AGENT": REPR_SENTINEL})

    assert REPR_SENTINEL not in repr(legacy_value)
    assert REPR_SENTINEL not in repr(canonical)
    assert REPR_SENTINEL not in repr(legacy)
    assert legacy.sec_edgar is not None
    assert legacy.sec_edgar.user_agent == REPR_SENTINEL
    assert REPR_SENTINEL not in json.dumps(
        legacy.symbol_intelligence_sec_edgar.diagnostic_summary()
    )


def test_new_configuration_is_distinct_and_legacy_runtime_configuration_is_unchanged() -> None:
    canonical = load_configuration({
        "ATLAS_SEC_EDGAR_ENABLED": "true",
        "ATLAS_SEC_EDGAR_USER_AGENT": CANONICAL_AGENT,
    })
    legacy = load_configuration({
        "SEC_EDGAR_USER_AGENT": LEGACY_AGENT,
        "SEC_EDGAR_FRESHNESS_DAYS": "2",
        "SEC_EDGAR_TIMEOUT_SECONDS": "4.5",
    })
    assert canonical.symbol_intelligence_sec_edgar.enabled is True
    assert canonical.sec_edgar is None
    assert legacy.symbol_intelligence_sec_edgar.enabled is True
    assert legacy.sec_edgar is not None
    assert legacy.sec_edgar.freshness_days == 2
    assert legacy.sec_edgar.timeout_seconds == 4.5
