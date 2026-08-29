"""The human-approval gate. No act-plane write happens without a GateDecision.

The loop is written so the gate is the ONLY thing standing between a proposed
patch and a cluster mutation. Which gate is wired in decides how much autonomy
the agent has, and the loop never needs to know which one it got:

  AutoApproveGate - approves automatically with an audit note, so the full
    lifecycle runs unattended for the demo.
  PagerDutyGate  - (live path, later) blocks on a real human ack for low and
    medium risk, and always holds high risk. Same interface, swapped in.
  DenyGate       - test double that proves the no-write path preserves the
    full audit trail and touches nothing.
"""
from __future__ import annotations

from .contracts import GateDecision, ProposedPatch


class AutoApproveGate:
    def request(self, patch: ProposedPatch) -> GateDecision:
        return GateDecision(
            approved=True,
            approver="auto-gate(demo)",
            note=(
                "Auto-approved for demo. Live path routes low/medium risk to a "
                "PagerDuty human ack and always holds high risk."
            ),
        )


class DenyGate:
    def request(self, patch: ProposedPatch) -> GateDecision:
        return GateDecision(
            approved=False,
            approver="deny-gate(test)",
            note="Denied by test gate: verifying the loop writes nothing on a hold.",
        )
