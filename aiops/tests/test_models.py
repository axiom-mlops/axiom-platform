from agent.models import Alert, Evidence, Severity
from agent.llm import FakeLLM


def make_alert() -> Alert:
    return Alert(
        fingerprint="abc123",
        name="HighMemoryUtilization",
        severity=Severity.critical,
        labels={"namespace": "boutique", "deployment": "recommendationservice"},
    )


def test_diagnose_returns_cited_hypotheses():
    ev = [Evidence(source="promql",
                   query="container_memory_working_set_bytes{...}",
                   result="memory at 94% of limit, CPU at 31%")]
    diag = FakeLLM().diagnose(make_alert(), ev)
    assert diag.top.confidence >= 0.5
    assert diag.top.supporting_evidence  # must cite evidence


def test_proposal_always_has_rollback():
    ev = [Evidence(source="promql", query="q", result="r")]
    diag = FakeLLM().diagnose(make_alert(), ev)
    patch = FakeLLM().propose(diag)
    assert patch.rollback_command
    assert patch.rationale
