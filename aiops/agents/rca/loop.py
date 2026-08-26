"""The RCA agent loop: observe -> diagnose -> propose -> gate -> execute -> verify -> document.

Design decision: this is a hand-rolled loop, not LangGraph/CrewAI.
Why: an incident-remediation agent has ONE correct control flow and it
must be auditable line-by-line — every state transition below is plain
Python a reviewer (or a change-approval board) can read in five minutes.
Frameworks earn their keep when the graph is dynamic; here the graph is
fixed and the risk is in the edges (what can execute, when), so the
loop IS the safety documentation. LangGraph is the right call when this
grows multi-agent coordination; the typed contracts port over as-is.

Safety properties, in order of importance:
  1. Whitelisted action space — the model picks from known-safe actions
     (contracts.ProposedPatch.action is a Literal), it never emits raw kubectl.
  2. Human gate before any write — act-plane tools are unreachable
     without an approved GateDecision.
  3. Verify after execute — the loop checks the signals actually
     recovered; an applied-but-ineffective patch is surfaced, not assumed.
  4. Everything is a typed record — the full IncidentRecord is the audit
     trail and the input to the self-written runbook.
"""
from __future__ import annotations

from .contracts import Alert, Diagnosis, Evidence, IncidentRecord
from .gate import AutoApproveGate
from .llm import DeterministicBackend, LLMBackend
from .tools import ActTools, ReadTools

INVESTIGATION_PLAN = [
    # (tool, query template) — the fixed evidence sweep for a saturation alert.
    ("prometheus", "container_memory_working_set_bytes{{pod=~'{svc}.*'}} / limit"),
    ("prometheus", "rate(container_cpu_usage_seconds_total{{pod=~'{svc}.*'}}[5m])"),
    ("kubernetes", "hpa/{svc} spec"),
    ("loki", '{{namespace="{ns}", pod=~"{svc}.*"}} |= "error"'),
]


def observe(alert: Alert, reads: ReadTools) -> list[Evidence]:
    """Evidence sweep. Deterministic plan, not LLM-chosen queries: for a
    known alert class the golden-signal sweep is the same every time, and
    a fixed plan is testable and rate-limit-safe. LLM-directed follow-up
    queries are the v2 upgrade once the base loop is trusted."""
    evidence = []
    for tool, template in INVESTIGATION_PLAN:
        query = template.format(svc=alert.service, ns=alert.namespace)
        evidence.append(getattr(reads, tool)(query))
    return evidence


def run_incident(
    alert: Alert,
    llm: LLMBackend | None = None,
    reads: ReadTools | None = None,
    acts: ActTools | None = None,
    gate=None,
) -> IncidentRecord:
    """Drive one alert through the full lifecycle. Returns the audit record."""
    llm = llm or DeterministicBackend()
    reads = reads or ReadTools()
    acts = acts or ActTools()
    gate = gate or AutoApproveGate()

    # 1. OBSERVE — gather evidence before reasoning (no diagnosis without data)
    evidence = observe(alert, reads)

    # 2. DIAGNOSE — structured RCA from the model, validated at the boundary
    diagnosis: Diagnosis = llm.diagnose(alert, evidence)

    # Low-confidence exit: escalate to a human instead of guessing.
    # An agent that knows when NOT to act is the difference between
    # an SRE tool and a pager-noise generator.
    if diagnosis.confidence == "low":
        patch = llm.propose(alert, diagnosis)
        from .contracts import GateDecision
        record = IncidentRecord(
            alert=alert, diagnosis=diagnosis, patch=patch,
            gate=GateDecision(approved=False, approver="loop-policy",
                              note="Auto-held: low diagnostic confidence, escalated to on-call."),
        )
        return record

    # 3. PROPOSE — a typed, whitelisted, human-reviewable remediation
    patch = llm.propose(alert, diagnosis)

    # 4. GATE — hard stop for human approval (PagerDuty in the live path)
    decision = gate.request(patch)
    record = IncidentRecord(alert=alert, diagnosis=diagnosis, patch=patch, gate=decision)
    if not decision.approved:
        return record  # audit trail preserved; nothing was touched

    # 5. EXECUTE — the only write in the loop, post-approval
    acts.execute(patch)

    # 6. VERIFY — did the signals actually recover?
    record.verification = acts.verify(patch, reads)
    return record


def write_runbook(record: IncidentRecord) -> str:
    """Self-writing runbook: the incident record rendered for the next human.
    Every field comes from the typed audit trail — nothing is invented."""
    d, p = record.diagnosis, record.patch
    lines = [
        f"# Runbook: {record.alert.alertname} — {record.alert.service}",
        "",
        f"**Severity:** {record.alert.severity} | **Namespace:** {record.alert.namespace} "
        f"| **Firing since:** {record.alert.firing_since}",
        "",
        "## Root cause",
        d.root_cause,
        "",
        "## Evidence",
        *[f"- `{e.source}` — `{e.query}` -> {e.finding}" for e in d.evidence],
        "",
        "## Remediation applied" if record.gate.approved else "## Remediation proposed (held)",
        f"- Action: `{p.action}` on `{p.namespace}/{p.target}` with {p.params}",
        f"- Rationale: {p.rationale}",
        f"- Risk: {p.risk} | Gate: {record.gate.approver} — {record.gate.note}",
    ]
    if record.verification:
        lines += ["", "## Verification",
                  *[f"- {c}" for c in record.verification.checks],
                  f"- Result: {'PASSED' if record.verification.passed else 'NOT RECOVERED'} "
                  f"| Residual risk: {record.verification.residual_risk}"]
    lines += ["", "## If this fires again",
              "1. Confirm the binding resource (memory vs CPU) before touching replica counts.",
              "2. Check HPA targets cover the binding resource.",
              "3. Horizontal scale beats limit raises for horizontally scalable services."]
    return "\n".join(lines)
