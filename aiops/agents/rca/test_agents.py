"""Specialist-layer tests: routing dispatches each alert class to the right
agent, all three specialists run end to end through the shared chassis, and the
new right-sizing action is guarded exactly like the others.

    python -m pytest rca/test_agents.py -v

Deterministic backends only, so no model or network is needed.
"""
import pytest

from rca.agents import build_registry, default_agent, route
from rca.contracts import Alert, PatchDeploymentResourcesParams, ProposedPatch
from rca.gate import DenyGate
from rca.loop import handle_alert


def _alert(name: str, svc: str = "cartservice") -> Alert:
    return Alert(
        fingerprint="f", alertname=name, severity="page", service=svc,
        namespace="boutique", summary="s", firing_since="2026-08-31T00:00:00Z",
    )


# --- routing ---------------------------------------------------------------- #
def test_router_dispatches_each_class_to_its_specialist():
    reg = build_registry()
    assert route(_alert("HighMemorySaturation"), reg).name == "rca-autoscaling"
    assert route(_alert("CanaryErrorRateHigh"), reg).name == "canary-verification"
    assert route(_alert("DeploymentOverprovisioned"), reg).name == "right-sizing"


def test_router_returns_none_for_unowned_alert():
    assert route(_alert("DiskFillingUp"), build_registry()) is None


def test_unowned_alert_escalates_rather_than_guessing():
    agent, record = handle_alert(_alert("DiskFillingUp"))
    assert agent is None and record is None


def test_default_agent_is_rca():
    assert default_agent().name == "rca-autoscaling"


# --- each specialist end to end -------------------------------------------- #
def test_rca_specialist_adds_memory_target_only():
    agent, record = handle_alert(_alert("HighMemorySaturation"))
    assert agent.name == "rca-autoscaling"
    assert record.patch.action == "patch_hpa_add_memory_target"
    assert record.patch.params == {"memory_target_average_utilization": 70}  # CPU untouched
    assert record.verification.passed is True


def test_canary_specialist_rolls_back():
    agent, record = handle_alert(_alert("CanaryErrorRateHigh", svc="checkoutservice"))
    assert agent.name == "canary-verification"
    assert record.patch.action == "rollback_deployment"
    assert record.patch.params == {"to_revision": None}
    assert record.verification.passed is True


def test_rightsizing_specialist_patches_resources():
    agent, record = handle_alert(_alert("DeploymentOverprovisioned", svc="productcatalogservice"))
    assert agent.name == "right-sizing"
    assert record.patch.action == "patch_deployment_resources"
    assert record.patch.params["cpu_request_millicores"] == 150
    assert record.patch.params["memory_request_mib"] == 256
    assert record.verification.passed is True


def test_denied_gate_writes_nothing_for_any_specialist():
    agent, record = handle_alert(_alert("CanaryErrorRateHigh"), gate=DenyGate())
    assert record.gate.approved is False
    assert record.verification is None  # executor never ran


# --- the new action is guarded like every other action --------------------- #
def test_patch_resources_params_accept_valid():
    m = PatchDeploymentResourcesParams(cpu_request_millicores=150, memory_request_mib=256)
    assert m.cpu_request_millicores == 150 and m.memory_request_mib == 256


def test_patch_resources_rejects_invented_key():
    with pytest.raises(Exception):
        PatchDeploymentResourcesParams(cpu_request_millicores=150, memory_request_mib=256, gpu=1)


def test_patch_resources_rejects_out_of_bounds():
    with pytest.raises(Exception):
        PatchDeploymentResourcesParams(cpu_request_millicores=0, memory_request_mib=256)


def test_proposed_patch_rejects_wrong_params_for_action():
    # scale params handed to the resources action must be rejected at the boundary
    with pytest.raises(Exception):
        ProposedPatch(
            action="patch_deployment_resources", target="t", namespace="n",
            params={"replicas": 3}, rationale="r", risk="low",
        )
