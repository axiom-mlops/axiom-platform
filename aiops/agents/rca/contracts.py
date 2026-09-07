"""Typed contracts for the RCA agent.

Design decision: every boundary in the loop (alert in, diagnosis out,
patch proposal out, verification out) is a Pydantic model, not a dict.
Why: LLM output is untrusted input. Validating it into a schema at the
boundary means a malformed model response fails loudly at parse time,
not silently at kubectl-patch time. This is the same principle as
validating user input at an API edge.

Params hole fix: the action was already constrained to a whitelist (a
Literal enum), but `params` was a bare dict, so the model could invent
key names and values. A tight action guarding a loose payload is not a
guardrail. Every action now has a typed params contract with extra keys
forbidden and numeric bounds enforced, wired in via a model_validator.
The generalizable rule: constrain every field the schema can constrain;
each unconstrained field is a place the model will drift.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Alert(BaseModel):
    """Normalized alert, source-agnostic (Alertmanager webhook shape subset)."""

    fingerprint: str
    alertname: str
    severity: Literal["page", "ticket", "info"]
    service: str
    namespace: str
    summary: str
    firing_since: str  # ISO8601; kept as str to stay serialization-friendly


class Evidence(BaseModel):
    """One observation the agent gathered while investigating."""

    source: Literal["prometheus", "loki", "kubernetes"]
    query: str
    finding: str


class Diagnosis(BaseModel):
    """The agent's structured RCA conclusion."""

    root_cause: str
    confidence: Literal["high", "medium", "low"]
    evidence: list[Evidence]
    blast_radius: str = Field(description="What breaks if this is left alone")
    prevention: list[str] = Field(
        default_factory=list,
        description="How to stop this alert class recurring; authored by the "
        "diagnosing specialist so the runbook footer matches the incident, not a "
        "hardcoded template. Empty is allowed (the renderer falls back).",
    )


# --------------------------------------------------------------------------- #
# Per-action params contracts. extra="forbid" rejects invented keys; numeric   #
# bounds reject nonsense values. These are the payload half of the whitelist.  #
# --------------------------------------------------------------------------- #
class HpaAddMemoryTargetParams(BaseModel):
    """Params for patch_hpa_add_memory_target.

    ADDITIVE and NON-DESTRUCTIVE. The model supplies ONLY the new memory target.
    The existing CPU target is current cluster state: it is read from the live
    HPA at execute time and preserved, never a value the model may set. HPA v2
    scales on the max desired replicas across metrics, so both targets end up
    live and a CPU-bound spike still scales; but "add a memory target" must not
    silently rewrite the operator's existing CPU threshold. Same principle as
    pinning target/namespace: the model decides the new thing, the system
    supplies observed facts. cpu is therefore NOT a settable param, and
    extra="forbid" means an attempt to set it is rejected at the boundary.
    """

    model_config = ConfigDict(extra="forbid")

    memory_target_average_utilization: int = Field(ge=1, le=100)


class ScaleDeploymentParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    replicas: int = Field(ge=1, le=100)


class RollbackDeploymentParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    to_revision: Optional[int] = Field(default=None, ge=1)  # None = previous revision


class PatchDeploymentResourcesParams(BaseModel):
    """Params for patch_deployment_resources (the right-sizing action).

    Right-sizing aligns requests (and optionally limits) to observed usage. It
    changes reserved capacity, not replica count, which is why it is a distinct
    action from scale_deployment. Values are integers in millicores and MiB so
    they are bounded and validated like every other action, rather than free
    Kubernetes quantity strings the model could mangle."""

    model_config = ConfigDict(extra="forbid")

    cpu_request_millicores: int = Field(ge=1, le=64000)
    memory_request_mib: int = Field(ge=1, le=131072)
    cpu_limit_millicores: Optional[int] = Field(default=None, ge=1, le=64000)
    memory_limit_mib: Optional[int] = Field(default=None, ge=1, le=131072)


# The registry is the single source of truth mapping an action to its payload
# contract. Adding a new action means adding a params model here and to the
# ProposedPatch.action Literal, and both halves of the guardrail move together.
PARAMS_BY_ACTION: dict[str, type[BaseModel]] = {
    "patch_hpa_add_memory_target": HpaAddMemoryTargetParams,
    "scale_deployment": ScaleDeploymentParams,
    "rollback_deployment": RollbackDeploymentParams,
    "patch_deployment_resources": PatchDeploymentResourcesParams,
}

# Risk tier is a property of the ACTION, not an opinion the model gets to hold.
# A rollback moves live traffic between revisions: it is medium by policy, and no
# amount of model confidence should be able to score it "low" and thereby weaken
# the gate. So risk is system-assigned from this map, the same principle as the
# CPU target and target/namespace being system-supplied: the model decides WHAT
# to do; the system owns the known facts about that action.
RISK_BY_ACTION: dict[str, str] = {
    "patch_hpa_add_memory_target": "low",
    "scale_deployment": "low",
    "rollback_deployment": "medium",
    "patch_deployment_resources": "low",
}


class ProposedPatch(BaseModel):
    """A concrete, human-reviewable remediation. Never free text.

    The agent may only propose actions from a whitelisted action space, AND
    the params for that action must match the registered per-action contract.
    The LLM chooses WHICH known-safe action fits and supplies its parameters;
    it cannot invent an action, and it cannot invent parameter keys or values.
    """

    action: Literal[
        "patch_hpa_add_memory_target",
        "scale_deployment",
        "rollback_deployment",
        "patch_deployment_resources",
    ]
    target: str
    namespace: str
    params: dict
    rationale: str
    # System-assigned from RISK_BY_ACTION in the validator below, NOT model-set.
    # Optional here only so callers need not supply it; it is always populated
    # after validation. Any value passed in is overwritten by policy.
    risk: Optional[Literal["low", "medium", "high"]] = None

    @model_validator(mode="after")
    def _validate_params_and_assign_risk(self) -> "ProposedPatch":
        """Two boundary jobs. First, validate params against the contract for the
        chosen action and normalize to the canonical typed dict; unknown keys or
        out-of-bounds values raise here, before anything reaches the act plane.
        Second, assign risk from policy for the action, OVERRIDING any supplied
        value, so risk is a system property of the action and never a number the
        model talked its way into. Assignment does not re-trigger this validator
        (validate_assignment is off), so the mutation is safe."""
        contract = PARAMS_BY_ACTION.get(self.action)
        if contract is None:
            raise ValueError(f"no params contract registered for action {self.action!r}")
        validated = contract.model_validate(self.params)  # extra=forbid + bounds
        self.params = validated.model_dump()
        self.risk = RISK_BY_ACTION[self.action]  # system-assigned, not model-chosen
        return self


class GateDecision(BaseModel):
    approved: bool
    approver: str
    note: Optional[str] = None


class VerificationResult(BaseModel):
    passed: bool
    checks: list[str]
    residual_risk: str


class IncidentRecord(BaseModel):
    """Everything the loop produced for one incident, feeds the runbook writer."""

    alert: Alert
    diagnosis: Diagnosis
    patch: ProposedPatch
    gate: GateDecision
    verification: Optional[VerificationResult] = None
