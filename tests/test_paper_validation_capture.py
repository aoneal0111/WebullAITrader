import json

from app.diagnostics.paper_validation_capture import PaperValidationCapture


def _provider():
    return {
        "runtime": {"health": "RUNNING"},
        "candidate_samples": [{"symbol": "WHLR", "score": 80}],
        "funnel_totals": {"warrior_evaluated": 1},
        "reason_code_totals": {"STOP_TOO_WIDE": 1},
    }


def _records(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_disabled_capture_creates_no_files(tmp_path):
    capture = PaperValidationCapture(_provider, output_dir=tmp_path)
    capture.start()
    capture.sample()
    capture.close()
    assert list(tmp_path.iterdir()) == []


def test_capture_writes_periodic_and_final_summary(tmp_path):
    capture = PaperValidationCapture(_provider, output_dir=tmp_path, enabled=True)
    capture.start()
    capture.sample()
    capture.close()

    path = capture.path
    assert path is not None and path.exists()
    assert (tmp_path / "LATEST.txt").read_text(encoding="utf-8").strip() == str(path.resolve())
    kinds = {record["record_type"] for record in _records(path)}
    assert {"session_start", "periodic_snapshot", "session_summary"} <= kinds


def test_bounds_and_previous_sessions_are_preserved(tmp_path):
    old = tmp_path / "old-session.jsonl"
    old.write_text("old\n", encoding="utf-8")
    provider = lambda: {
        "candidate_samples": [{"symbol": str(index)} for index in range(20)]
    }
    capture = PaperValidationCapture(
        provider,
        output_dir=tmp_path,
        enabled=True,
        max_candidate_samples=2,
        max_transitions=2,
    )
    capture.start()
    for index in range(10):
        capture.record_transition("TEST", index, index + 1)
    capture.sample()
    capture.close()

    assert old.exists()
    summary = next(record for record in _records(capture.path) if record["record_type"] == "session_summary")
    assert len(summary["candidate_samples"]) <= 2
    assert summary["transition_count"] <= 2


def test_provider_or_writer_failure_is_contained_and_sanitized(tmp_path, monkeypatch):
    def failing_provider():
        raise RuntimeError("secret exception text")

    capture = PaperValidationCapture(failing_provider, output_dir=tmp_path, enabled=True)
    capture.start()
    capture.sample()

    capture.record_transition(
        "WARRIOR",
        "RUNNING",
        "DEGRADED",
        reason_category="critical_capture",
        exception_class="RuntimeError: secret exception text",
    )
    monkeypatch.setattr("app.diagnostics.paper_validation_capture.json.dumps", lambda *_a, **_k: (_ for _ in ()).throw(OSError("disk")))
    capture.close()

    assert capture._writer_failures >= 1
    assert capture._dropped_records >= 0
    assert "secret exception text" not in repr(capture._transitions)
    assert all("RuntimeError" in str(item.get("exception_class")) for item in capture._transitions)
