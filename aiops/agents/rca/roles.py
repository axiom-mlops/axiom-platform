"""The three role-agents of the incident-remediation pipeline.

This is the multi-agent decomposition of the loop. Each agent has ONE
responsibility, a typed input, and a typed output, so each can be built,
tested, and swapped independently, and each can run a different reasoning
backend:

  DiagnosticianAgent  OBSERVE + DIAGNOSE. Alert in, Diagnosis out. Runs the
    domain SLM (fine-tuned in prod). Its evidence sweep is fixed and
    deterministic, so the same alert class is investigated the same way every
    time (testable, rate-limit-safe).

  RemediationPlannerAgent  DESIGN + PROPOSE. Alert + Diagnosis in, a typed,
    whitelisted, validated ProposedPatch out. It treats the Diagnosis as
    untrusted input, because from the Planner's seat another agent's output IS
    untrusted input; the contract validation in contracts.py enforces that.

  ExecutorAgent  EXECUTE + VERIFY. An approved ProposedPatch in, a
    VerificationResult out. Deliberately has NO model: the only actor that
    writes to a cluster is pure deterministic policy, because the safety-
    critical agent must be the most auditable one, not the least. It is
    unreachable without an approved gate (the orchestrator enforces that).

The handoffs between agents (Diagnosis, ProposedPatch) are the inter-agent
contracts. Validating them is not ceremony: it means a misbehaving upstream
agent cannot corrupt a downstream one.
"""
from __future__ import annotations

from .contracts import Alert, Diagnosis, Evidence, ProposedPatch, VerificationResult
from .llm import LLMBackend
from .tools import ActTools, ReadTools

INVESTIGATION_PLAN = [
    # (tool, query template) — the fixed evidence sweep for a saturation alert.
    ("prometheus", "container_memory_working_set_bytes{{pod=~'{svc}.*'}} / limit"),
    ("prometheus", "rate(container_cpu_usage_seconds_total{{pod=~'{svc}.*'}}[5m])"),
    ("kubernetes", "hpa/{svc} spec"),
    ("loki", '{{namespace="{ns}", pod=~"{svc}.*"}} |= "error"'),
]


def observe(alert: Alert, reads: ReadTools, plan=INVESTIGATION_PLAN) -> list[Evidence]:
    """Evidence sweep. Deterministic plan, not LLM-chosen queries: for a known
    alert class the golden-signal sweep is the same every time, and a fixed plan
    is testable and rate-limit-safe. LLM-directed follow-up is a later upgrade.

    The plan is a parameter so each specialist (see agents.py) sweeps the signals
    that matter for its problem class, while the role and its safety properties
    stay identical. Defaults to the saturation sweep for backward compatibility."""
    evidence: list[Evidence] = []
    for tool, template in plan:
        query = template.format(svc=alert.service, ns=alert.namespace)
        evidence.append(getattr(reads, tool)(query))
    return evidence


class DiagnosticianAgent:
    """Role: observe then diagnose. Alert -> Diagnosis. Parameterized by the
    specialist's evidence plan and reasoning backend."""

    def __init__(self, llm: LLMBackend, reads: ReadTools, plan=INVESTIGATION_PLAN):
        self.llm = llm
        self.reads = reads
        self.plan = plan

    def run(self, alert: Alert) -> Diagnosis:
        evidence = observe(alert, self.reads, self.plan)
        return self.llm.diagnose(alert, evidence)


class RemediationPlannerAgent:
    """Agent 2: design and propose. Alert + Diagnosis -> ProposedPatch."""

    def __init__(self, llm: LLMBackend):
        self.llm = llm

    def run(self, alert: Alert, diagnosis: Diagnosis) -> ProposedPatch:
        return self.llm.propose(alert, diagnosis)


class ExecutorAgent:
    """Agent 3: execute then verify. Approved ProposedPatch -> VerificationResult.

    No LLM by design. The orchestrator only calls this after an approved gate,
    so reaching execute() at all already implies human (or policy) approval."""

    def __init__(self, acts: ActTools, reads: ReadTools):
        self.acts = acts
        self.reads = reads

    def run(self, patch: ProposedPatch) -> VerificationResult:
        self.acts.execute(patch)               # the only write in the system
        return self.acts.verify(patch, self.reads)
