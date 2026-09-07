"""Boundary tests for the ProposedPatch params guardrail.

Run from the directory containing the rca package (sre/aiops/agents):

    python -m pytest rca/test_contracts.py -v

These assert the hole is closed: the action whitelist held before, but params
was a bare dict. Now the payload is validated per action.
"""
import pytest
from pydantic import ValidationError

from rca.contracts import ProposedPatch


def _hpa_patch(**params) -> ProposedPatch:
    return ProposedPatch(
        action="patch_hpa_add_memory_target",
        target="cartservice",
        namespace="boutique",
        params=params,
        rationale="test",
        risk="low",
    )


def test_valid_params_accepted_and_normalized():
    p = _hpa_patch(memory_target_average_utilization=70)
    # Model supplies only the new memory target; CPU is preserved at execute time.
    assert p.params == {"memory_target_average_utilization": 70}


def test_model_cannot_set_cpu_target():
    # CPU is observed state, not a model decision. Trying to set it is rejected,
    # so "add a memory target" can never silently rewrite the existing CPU target.
    with pytest.raises(ValidationError):
        _hpa_patch(memory_target_average_utilization=80, cpu_target_average_utilization=60)


def test_rejects_the_exact_base_model_drift():
    # This is verbatim what qwen3.5:9b emitted before the fix: invented keys,
    # duplicated namespace/service into params. It must now be rejected.
    with pytest.raises(ValidationError):
        _hpa_patch(
            namespace="boutique",
            service="cartservice",
            metric_type="memory",
            target_utilization_percentage=80,
        )


def test_rejects_out_of_bounds():
    with pytest.raises(ValidationError):
        _hpa_patch(memory_target_average_utilization=250)


def test_rejects_missing_required_memory_target():
    with pytest.raises(ValidationError):
        _hpa_patch(cpu_target_average_utilization=70)


def test_scale_deployment_params():
    p = ProposedPatch(action="scale_deployment", target="c", namespace="b",
                      params={"replicas": 5}, rationale="t", risk="low")
    assert p.params == {"replicas": 5}


def test_scale_deployment_rejects_extra_keys():
    with pytest.raises(ValidationError):
        ProposedPatch(action="scale_deployment", target="c", namespace="b",
                      params={"replicas": 5, "surprise": 1}, rationale="t", risk="low")


def test_rollback_defaults_to_previous():
    p = ProposedPatch(action="rollback_deployment", target="c", namespace="b",
                      params={}, rationale="t", risk="medium")
    assert p.params == {"to_revision": None}


def test_risk_is_system_assigned_overriding_input():
    # Finding from Block B drift analysis: the model scored a rollback "low".
    # Risk is a property of the action, not the model's opinion, so even a patch
    # constructed with risk="low" must come out "medium" for a rollback.
    p = ProposedPatch(action="rollback_deployment", target="c", namespace="b",
                      params={}, rationale="t", risk="low")
    assert p.risk == "medium"


def test_risk_assigned_when_not_supplied():
    # The model no longer supplies risk at all; the boundary fills it from policy.
    p = ProposedPatch(action="patch_hpa_add_memory_target", target="c",
                      namespace="b", params={"memory_target_average_utilization": 70},
                      rationale="t")
    assert p.risk == "low"
