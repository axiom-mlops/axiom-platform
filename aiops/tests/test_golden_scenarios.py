"""Golden scenarios: the agent's behavioral contract, run in CI.

These pin the loop's safety properties, not model quality:
the gate always precedes execution, denial means zero writes,
low confidence means escalation, and the runbook reflects the record.
"""
from agents.rca.contracts import Alert
from agents.rca.gate import AutoApproveGate, DenyGate
from agents.rca.loop import run_incident, write_runbook
from agents.rca.tools import ActTools, ReadTools


def make_alert(service="recommendationservice") -> Alert:
    return Alert(
        fingerprint="f01",
        alertname="ContainerMemoryNearLimit",
        severity="page",
        service=service,
        namespace="boutique",
        summary="Working set >90% of limit for 10m",
        firing_since="2026-08-24T19:02:00Z",
    )


def test_happy_path_diagnoses_hpa_blindspot_and_verifies():
    acts = ActTools()
    record = run_incident(make_alert(), acts=acts)
    assert record.diagnosis.confidence == "high"
    assert "HPA" in record.diagnosis.root_cause
    assert record.patch.action == "patch_hpa_add_memory_target"
    assert record.gate.approved
    assert len(acts.executed) == 1
    assert record.verification and record.verification.passed


def test_denied_gate_means_zero_writes():
    acts = ActTools()
    record = run_incident(make_alert(), acts=acts, gate=DenyGate())
    assert not record.gate.approved
    assert acts.executed == []          # the safety property under test
    assert record.verification is None


def test_low_confidence_escalates_instead_of_acting():
    acts = ActTools()
    # Unknown service -> fixtures return "no data" -> FakeLLM low confidence
    record = run_incident(make_alert(service="unknownservice"), acts=acts)
    assert record.diagnosis.confidence == "low"
    assert not record.gate.approved
    assert acts.executed == []


def test_runbook_reflects_audit_trail():
    record = run_incident(make_alert())
    rb = write_runbook(record)
    assert "Root cause" in rb and "Verification" in rb
    assert "patch_hpa_add_memory_target" in rb
