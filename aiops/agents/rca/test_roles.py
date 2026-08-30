"""Per-agent tests: each role-agent works in isolation, and the pipeline works
end to end. Uses the deterministic backend, so no model or network is needed.

    python -m pytest rca/test_roles.py -v
"""
from rca.contracts import Alert, Diagnosis, ProposedPatch, VerificationResult
from rca.gate import AutoApproveGate, DenyGate
from rca.llm import DeterministicBackend
from rca.loop import run_incident
from rca.roles import DiagnosticianAgent, ExecutorAgent, RemediationPlannerAgent
from rca.tools import ActTools, ReadTools


def _alert() -> Alert:
    return Alert(
        fingerprint="t", alertname="HighMemorySaturation", severity="page",
        service="cartservice", namespace="boutique",
        summary="memory working set >90% while HPA at max replicas, CPU low",
        firing_since="2026-08-28T00:00:00Z",
    )


def test_diagnostician_observes_then_diagnoses():
    d = DiagnosticianAgent(DeterministicBackend(), ReadTools()).run(_alert())
    assert isinstance(d, Diagnosis)
    assert d.confidence in ("high", "medium", "low")
    assert len(d.evidence) == 4                      # the fixed sweep ran
    assert "memory" in d.root_cause.lower()


def test_planner_returns_whitelisted_validated_patch():
    a = _alert()
    d = DiagnosticianAgent(DeterministicBackend(), ReadTools()).run(a)
    p = RemediationPlannerAgent(DeterministicBackend()).run(a, d)
    assert isinstance(p, ProposedPatch)
    assert p.action == "patch_hpa_add_memory_target"
    assert p.params == {"memory_target_average_utilization": 70}   # CPU not touched


def test_executor_executes_and_verifies():
    a = _alert()
    d = DiagnosticianAgent(DeterministicBackend(), ReadTools()).run(a)
    p = RemediationPlannerAgent(DeterministicBackend()).run(a, d)
    v = ExecutorAgent(ActTools(dry_run=True), ReadTools()).run(p)
    assert isinstance(v, VerificationResult)
    assert v.passed is True


def test_pipeline_end_to_end_approved():
    rec = run_incident(_alert())                      # deterministic, auto-approved
    assert rec.gate.approved is True
    assert rec.verification is not None
    assert rec.patch.params == {"memory_target_average_utilization": 70}


def test_pipeline_denied_gate_writes_nothing():
    rec = run_incident(_alert(), gate=DenyGate())
    assert rec.gate.approved is False
    assert rec.verification is None                   # executor never ran
