"""Orchestrator for the incident-remediation pipeline.

The pipeline is three role-agents (see roles.py) coordinated here:

    DiagnosticianAgent -> RemediationPlannerAgent -> [human gate] -> ExecutorAgent

Design decision: the orchestrator is hand-rolled, not LangGraph/CrewAI. An
incident-remediation pipeline has ONE correct control flow and it must be
auditable line-by-line; every transition below is plain Python a reviewer or a
change-approval board can read in five minutes. Frameworks earn their keep when
the graph is dynamic. Here the graph is fixed and the risk lives in the edges
(what can execute, when), so the orchestrator IS the safety documentation. The
typed contracts and role-agents port over unchanged if this later grows dynamic
multi-agent coordination.

Safety properties, in order of importance:
  1. Whitelisted action space + typed per-action params (contracts.py) — the
     Planner picks from known-safe actions and cannot invent parameters.
  2. Human gate before any write — the ExecutorAgent is only ever invoked
     after an approved GateDecision.
  3. Verify after execute — the Executor checks the signals actually recovered.
  4. Everything is a typed record — the IncidentRecord is the audit trail and
     the input to the self-written runbook.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .contracts import Alert, Diagnosis, GateDecision, IncidentRecord
from .gate import AutoApproveGate
from .llm import DeterministicBackend, LLMBackend
from .roles import (
    INVESTIGATION_PLAN,
    DiagnosticianAgent,
    ExecutorAgent,
    RemediationPlannerAgent,
)
from .tools import ActTools, ReadTools

if TYPE_CHECKING:
    from .agents import Agent


def run_incident(
    alert: Alert,
    agent: "Agent | None" = None,
    llm: LLMBackend | None = None,
    reads: ReadTools | None = None,
    acts: ActTools | None = None,
    gate=None,
    planner_llm: LLMBackend | None = None,
) -> IncidentRecord:
    """Drive one alert through the three role-agents. Returns the audit record.

    Two ways to call it. Pass a specialist `agent` (from agents.py) and its
    backend and evidence plan drive the pipeline. Or pass `llm` directly (and
    the default saturation plan is used) for the RCA path or tests. `planner_llm`
    optionally gives the Planner a different brain (the hybrid path: SLM
    diagnoses, frontier plans). The Executor takes no model, by design."""
    if agent is not None:
        llm = agent.backend
        plan = agent.plan
    else:
        llm = llm or DeterministicBackend()
        plan = INVESTIGATION_PLAN

    reads = reads or ReadTools()
    acts = acts or ActTools()
    gate = gate or AutoApproveGate()

    diagnostician = DiagnosticianAgent(llm, reads, plan)
    planner = RemediationPlannerAgent(planner_llm or llm)
    executor = ExecutorAgent(acts, reads)

    # 1. DIAGNOSE (Diagnostician: observe -> RCA)
    diagnosis: Diagnosis = diagnostician.run(alert)

    # Low-confidence exit: escalate to a human instead of guessing. An agent
    # that knows when NOT to act is the difference between an SRE tool and a
    # pager-noise generator. We still record the proposal for the audit trail.
    if diagnosis.confidence == "low":
        patch = planner.run(alert, diagnosis)
        return IncidentRecord(
            alert=alert, diagnosis=diagnosis, patch=patch,
            gate=GateDecision(approved=False, approver="loop-policy",
                              note="Auto-held: low diagnostic confidence, escalated to on-call."),
        )

    # 2. PROPOSE (Planner: design -> typed, whitelisted remediation)
    patch = planner.run(alert, diagnosis)

    # 3. GATE (hard stop for human approval; PagerDuty in the live path)
    decision = gate.request(patch)
    record = IncidentRecord(alert=alert, diagnosis=diagnosis, patch=patch, gate=decision)
    if not decision.approved:
        return record  # audit trail preserved; nothing was touched

    # 4. EXECUTE + VERIFY (Executor: the only write, post-approval)
    record.verification = executor.run(patch)
    return record


def handle_alert(alert: Alert, registry=None, reads=None, acts=None, gate=None):
    """Router entrypoint: pick the specialist that owns this alert class, then
    run it through the pipeline. Returns (agent, record). If no specialist
    matches, returns (None, None): the system escalates to a human rather than
    letting the wrong agent guess."""
    from .agents import build_registry, route

    registry = build_registry() if registry is None else registry
    agent = route(alert, registry)
    if agent is None:
        return None, None
    return agent, run_incident(alert, agent=agent, reads=reads, acts=acts, gate=gate)


def write_runbook(record: IncidentRecord) -> str:
    """Self-writing runbook: the incident record rendered for the next human.
    Every field comes from the typed audit trail, nothing is invented."""
    d, p = record.diagnosis, record.patch
    lines = [
        f"# Runbook: {record.alert.alertname} - {record.alert.service}",
        "",
        f"**Severity:** {record.alert.severity} | **Namespace:** {record.alert.namespace} "
        f"| **Firing since:** {record.alert.firing_since}",
        "",
        "## Root cause",
        d.root_cause,
        "",
        "## Evidence",
        *[f"- `{e.source}` - `{e.query}` -> {e.finding}" for e in d.evidence],
        "",
        "## Remediation applied" if record.gate.approved else "## Remediation proposed (held)",
        f"- Action: `{p.action}` on `{p.namespace}/{p.target}` with {p.params}",
        f"- Rationale: {p.rationale}",
        f"- Risk: {p.risk} | Gate: {record.gate.approver} - {record.gate.note}",
    ]
    if record.verification:
        lines += ["", "## Verification",
                  *[f"- {c}" for c in record.verification.checks],
                  f"- Result: {'PASSED' if record.verification.passed else 'NOT RECOVERED'} "
                  f"| Residual risk: {record.verification.residual_risk}"]
    steps = d.prevention or [
        "Review the diagnosis and evidence above and confirm the binding signal "
        "before acting."
    ]
    lines += ["", "## If this fires again",
              *[f"{i}. {s}" for i, s in enumerate(steps, 1)]]
    return "\n".join(lines)
