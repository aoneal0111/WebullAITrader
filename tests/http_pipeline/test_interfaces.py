from app.http_pipeline import HTTPRequestPipeline


def test_pipeline_protocol_operations_are_exact():
    public = {name for name in HTTPRequestPipeline.__dict__ if not name.startswith("_")}
    assert public == {"prepare", "finalize"}
