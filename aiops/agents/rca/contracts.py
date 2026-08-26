"""Typed contracts for the RCA agent.

Design decision: every boundary in the loop (alert in, diagnosis out,
patch proposal out, verification out) is a Pydantic model, not a dict.
Why: LLM output is untrusted input. Validating it into a schema at the
boundary means a malformed model response fails loudly at parse time,
not silently at kubectl-patch time. This is the same principle as
validating user input at an API edge.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


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


class ProposedPatch(BaseModel):
    """A concrete, human-reviewable remediation. Never free text.

    The agent may only propose actions from a whitelisted action space.
    This is the core safety property: the LLM chooses *which* known-safe
    action fits, it does not invent arbitrary kubectl commands.
    """

    action: Literal["patch_hpa_add_memory_target", "scale_deployment", "rollback_deployment"]
    target: str
    namespace: str
    params: dict
    rationale: str
    risk: Literal["low", "medium", "high"]


class GateDecision(BaseModel):
    approved: bool
    approver: str
    note: Optional[str] = None


class VerificationResult(BaseModel):
    passed: bool
    checks: list[str]
    residual_risk: str


class IncidentRecord(BaseModel):
    """Everything the loop produced for one incident — feeds the runbook writer."""

    alert: Alert
    diagnosis: Diagnosis
    patch: ProposedPatch
    gate: GateDecision
    verification: Optional[VerificationResult] = None
