from copy import deepcopy
from decimal import localcontext
import json
import subprocess
import sys
import pytest

from app.asset_modules.engine_catalog import EngineId
from app.asset_modules.strategy_comparison import summarize


def fixture():
    return {"version": "ENGINE_COMPARISON_V1", "experiment_id": "frozen-entry-v1",
            "dataset_id": "capture-sha256", "cost_model_id": "fees-spread-v1",
            "evidence_kind": "QUOTE_PROXY",
            "engines": {e.value: {"policies": ["champion-v1", "challenger-v1"],
                                  "champion": "champion-v1"} for e in EngineId},
            "results": []}


def row(engine=EngineId.WARRIOR, episode="one", policy="champion-v1", pnl="-25", **changes):
    result = dict(engine=str(engine), episode_id=episode, policy=policy,
                  instrument="SPY", trading_date="2026-10-09", status="CLOSED", net_pnl=pnl)
    result.update(changes)
    return result


def test_pairs_only_same_closed_cohort_and_separates_engines():
    data = fixture()
    data["results"] = [row(), row(policy="challenger-v1", pnl="10"),
                       row(episode="missing", pnl="1000"),
                       row(episode="unresolved", pnl="1000"),
                       row(episode="unresolved", policy="challenger-v1", status="UNRESOLVED", pnl=None),
                       row(engine=EngineId.SCALPER, pnl="200"),
                       row(engine=EngineId.SCALPER, policy="challenger-v1", pnl="300")]
    out = summarize(data)
    warrior, scalper, crypto, *_ = out["engines"]
    assert warrior["paired_closed_episodes"] == 1
    assert warrior["excluded_from_paired_totals"] == 2
    assert warrior["policies"][0]["paired_net_pnl"] == "-25"
    assert warrior["policies"][1]["paired_delta_vs_champion"] == "35"
    assert warrior["policies"][1]["status_counts"] == {"CLOSED": 1, "MISSING": 1, "UNRESOLVED": 1}
    assert scalper["policies"][0]["paired_net_pnl"] == "200"
    assert crypto["policies"][0]["paired_net_pnl"] is None
    assert out["promotion"] == "NONE"


def test_counts_dependence_and_exact_cost_inclusive_amounts():
    data = fixture()
    data["results"] = [row(episode=ep, policy=policy, pnl=pnl)
                       for ep, pnl in [("a", "0.10"), ("b", "-0.05")]
                       for policy in ["champion-v1", "challenger-v1"]]
    report = summarize(data)["engines"][0]
    assert report["paired_symbol_dates"] == report["paired_trading_dates"] == 1
    assert report["paired_closed_episodes"] == 2
    p = report["policies"][0]
    assert p["paired_net_pnl"] == "0.05"
    assert p["paired_mean_net_pnl"] == "0.025"
    assert p["paired_profit_factor"] == "2"


def test_summary_ignores_callers_decimal_precision():
    data = fixture()
    data["results"] = [row(policy=p, pnl="12345.678901")
                       for p in ["champion-v1", "challenger-v1"]]
    with localcontext() as context:
        context.prec = 3
        out = summarize(data)
    assert out["engines"][0]["policies"][0]["paired_net_pnl"] == "12345.678901"


@pytest.mark.parametrize("change", [
    {"net_pnl": "NaN"}, {"net_pnl": "Infinity"}, {"net_pnl": True},
    {"net_pnl": 0.1}, {"net_pnl": "1e13"}, {"net_pnl": "invalid"},
    {"net_pnl": "0e-999999"}, {"evidence_kind": "ACTUAL_PAPER_FILLS"},
    {"status": "UNRESOLVED"}, {"status": "NO_ENTRY"}, {"status": "OPEN"},
    {"policy": "undeclared"}, {"engine": "OTHER"}, {"trading_date": "2026-02-30"},
    {"episode_id": ""}, {"instrument": ""},
])
def test_rejects_ambiguous_results(change):
    data = fixture()
    data["results"] = [row(**change)]
    with pytest.raises(ValueError):
        summarize(data)


def test_duplicate_or_changed_episode_context_rejected():
    data = fixture()
    data["results"] = [row(), row()]
    with pytest.raises(ValueError, match="Duplicate"):
        summarize(data)
    data["results"] = [row(), row(policy="challenger-v1", instrument="QQQ")]
    with pytest.raises(ValueError, match="context mismatch"):
        summarize(data)


@pytest.mark.parametrize("field,value", [
    ("version", "v0"), ("evidence_kind", "MIXED"), ("experiment_id", ""),
    ("dataset_id", None), ("cost_model_id", ""), ("engines", {}),
    ("results", None),
])
def test_requires_frozen_experiment_provenance(field, value):
    data = fixture()
    data[field] = value
    with pytest.raises(ValueError):
        summarize(data)


def test_all_declared_policies_must_close_and_input_not_mutated():
    data = fixture()
    data["engines"][EngineId.WARRIOR]["policies"].append("third-v1")
    data["results"] = [row(), row(policy="challenger-v1")]
    before = deepcopy(data)
    assert summarize(data)["engines"][0]["paired_closed_episodes"] == 0
    assert data == before


@pytest.mark.parametrize("spec", [
    {"policies": ["a"], "champion": "a"},
    {"policies": ["a", "a"], "champion": "a"},
    {"policies": ["a", "b"], "champion": "c"},
])
def test_invalid_policy_declaration(spec):
    data = fixture()
    data["engines"][EngineId.WARRIOR] = spec
    with pytest.raises(ValueError):
        summarize(data)


def test_cli_accepts_powershell_bom_and_returns_no_data_explicitly(tmp_path):
    path = tmp_path / "comparison.json"
    path.write_text(json.dumps(fixture()), encoding="utf-8-sig")
    result = subprocess.run([sys.executable, "-m", "app.asset_modules.strategy_comparison", str(path)],
                            capture_output=True, text=True, check=True)
    assert json.loads(result.stdout)["engines"][0]["policies"][0]["paired_net_pnl"] is None


def test_cli_rejects_malformed_input_without_report(tmp_path):
    path = tmp_path / "comparison.json"
    path.write_text('{"version":"bad"}', encoding="utf-8")
    result = subprocess.run([sys.executable, "-m", "app.asset_modules.strategy_comparison", str(path)],
                            capture_output=True, text=True)
    assert result.returncode != 0
    assert not result.stdout
