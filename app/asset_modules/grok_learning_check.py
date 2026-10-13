"""Offline synthetic learning journal check; never constructs an API client."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from app.asset_modules.engine_catalog import EngineId
from app.asset_modules.grok_learning import LearningJournal


def check(root):
    data = {"version": "ENGINE_COMPARISON_V1", "experiment_id": "synthetic-learning-v1",
            "dataset_id": "synthetic-20-episodes", "cost_model_id": "declared-synthetic-net",
            "evidence_kind": "MINUTE_BAR_PROXY", "engines": {}, "results": []}
    for engine in EngineId:
        data["engines"][engine.value] = {"champion": "baseline",
            "policies": ["baseline", "high-win-losing", "positive-expectancy"]}
        for i in range(20):
            amounts = {"baseline": "-1", "high-win-losing": "5" if i < 17 else "-40",
                       "positive-expectancy": "10" if i < 10 else "-5"}
            for policy, pnl in amounts.items():
                data["results"].append({"engine": engine.value, "policy": policy,
                    "episode_id": str(i), "instrument": "SYNTHETIC", "trading_date": "2026-10-09",
                    "status": "CLOSED", "net_pnl": pnl})
    journal = LearningJournal(Path(root) / "learning.sqlite3")
    journal.ingest(data)
    preview = journal.review(data["experiment_id"])
    class Mock:
        calls = 0
        def request(self, role, instruction, context, schema):
            self.calls += 1
            assert role == "LEARNING"
            return {"candidate_ids": [c["id"] for c in context["candidates"]],
                    "reason": "Synthetic nomination only; requires new paper evidence."}
    mock = Mock()
    result = journal.review(data["experiment_id"], client=mock)
    recovered = LearningJournal(journal.path).review(data["experiment_id"], client=mock)
    passed = (len(preview["candidates"]) == 5 and len(result["selected"]) == 5
              and all(c["policy"] == "positive-expectancy" for c in result["selected"])
              and recovered == result and mock.calls == 1 and result["promotion"] == "NONE")
    return {"scope": "SYNTHETIC_LEARNING_REVIEW_NOT_TRADING_PERFORMANCE",
            "passed": passed, "engines": 5, "high_win_negative_pnl_candidate_excluded": True,
            "mock_reviews": mock.calls, "review_recovered": recovered == result,
            "network_requests": 0, "orders": 0, "promotion": "NONE"}


def main():
    with TemporaryDirectory(prefix="atlas-learning-") as root:
        report = check(root)
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
