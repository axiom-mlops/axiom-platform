"""Human approval gate — the agent's hard safety boundary.

Design decision: the gate sits between propose and execute, and it is
structural, not behavioral. The act-plane tools are simply unreachable
until a GateDecision(approved=True) exists. A prompt can't talk its way
past an interface.

Live implementation: PagerDuty incident with the ProposedPatch rendered
as the remediation note; responder ack = approval. CLI implementation
below is for local demos and tests.
"""
from __future__ import annotations

from .contracts import GateDecision, ProposedPatch


class AutoApproveGate:
    """Test/demo gate. Records what it approved so tests can assert on it."""

    def __init__(self, approver: str = "demo-operator"):
        self.approver = approver
        self.seen: list[ProposedPatch] = []

    def request(self, patch: ProposedPatch) -> GateDecision:
        self.seen.append(patch)
        return GateDecision(approved=True, approver=self.approver,
                            note="Approved: low-risk, whitelisted action, evidence reviewed.")


class DenyGate:
    def request(self, patch: ProposedPatch) -> GateDecision:
        return GateDecision(approved=False, approver="policy",
                            note="Denied by policy — action held for human review.")
