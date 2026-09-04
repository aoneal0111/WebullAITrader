from datetime import UTC, datetime
import json

from app.entry_opportunity_value import JsonLinesObservationStore, evaluate_entry_opportunity
from tests.entry_opportunity_value.test_evaluator import entry_context


def test_jsonl_store_is_separate_append_only_research_persistence(tmp_path):
    path = tmp_path / "entry-value" / "observations.jsonl"
    store = JsonLinesObservationStore(path)
    observation = evaluate_entry_opportunity(entry_context())
    store.append(observation)
    store.append(observation)
    store.close()
    rows = path.read_text(encoding="utf-8").splitlines()
    assert len(rows) == 2
    payload = json.loads(rows[0])
    assert payload["research_only"] is True
    assert payload["execution_authorized"] is False
    assert payload["context"]["planned_entry_price"] == "10.00"
