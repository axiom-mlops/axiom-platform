"""LLM backend behind a swappable interface.

Design decision: the loop depends on a Protocol, not a vendor SDK.
Why: (1) golden-scenario tests run deterministically in CI at zero API
cost; (2) the serving backend is a deployment decision, not an
architecture decision. The same loop runs against a prompted frontier
model and, later, a fine-tuned 9B-class open-weights model served by
vLLM, with no changes to the loop.
"""
from __future__ import annotations

import json
from typing import Protocol

from .contracts import Alert, Diagnosis, Evidence, ProposedPatch


class LLMBackend(Protocol):
    """Contract every backend satisfies: structured in, structured out."""

    model_id: str

    def diagnose(self, alert: Alert, evidence: list[Evidence]) -> Diagnosis: ...

    def propose(self, alert: Alert, diagnosis: Diagnosis) -> ProposedPatch: ...


class DeterministicBackend:
    """Reference backend encoding the HPA memory blind-spot playbook.

    Not a model and not a mock of one -- it is the golden-scenario oracle.
    Its purpose is to make the loop's control flow, gating, and verification
    testable independently of model quality, so a backend swap becomes a
    measurable change rather than a leap of faith.

    Backends targeted for the live path:
      - frontier-class hosted model (baseline; establishes the quality bar)
      - 9B-class open-weights model, QLoRA-tuned, vLLM-served on-prem
    """

    model_id = "deterministic-reference-v1"

    def diagnose(self, alert: Alert, evidence: list[Evidence]) -> Diagnosis:
        mem = any("memory" in e.finding.lower() for e in evidence)
        cpu_low = any("cpu" in e.finding.lower() and "low" in e.finding.lower() for e in evidence)
        hpa_cpu_only = any("hpa targets: cpu only" in e.finding.lower() for e in evidence)
        if mem and cpu_low and hpa_cpu_only:
            return Diagnosis(
                root_cause=(
                    f"{alert.service} is memory-bound under load but its HPA "
                    "scales only on CPU utilization. Memory approaches the pod "
                    "limit while CPU stays under the HPA target, so no scale-out "
                    "occurs and pods head toward OOMKill."
                ),
                confidence="high",
                evidence=evidence,
                blast_radius=(
                    "OOMKill restarts under sustained load; request failures and "
                    "latency spikes propagate to checkout via the frontend."
                ),
            )
        return Diagnosis(
            root_cause="Insufficient evidence for a confident root cause.",
            confidence="low",
            evidence=evidence,
            blast_radius="Unknown",
        )

    def propose(self, alert: Alert, diagnosis: Diagnosis) -> ProposedPatch:
        return ProposedPatch(
            action="patch_hpa_add_memory_target",
            target=alert.service,
            namespace=alert.namespace,
            params={"memory_target_utilization": 75},
            rationale=(
                "Add a memory utilization target (75%) alongside the existing CPU "
                "target so the HPA reacts to the actual binding resource. Chosen "
                "over raising the memory limit because the workload scales "
                "horizontally and a limit raise only delays the same failure."
            ),
            risk="low",
        )


def render_prompt(alert: Alert, evidence: list[Evidence]) -> str:
    """Instruction surface shared by every model-backed implementation, so the
    prompted baseline and the fine-tuned model are compared on equal footing."""
    return (
        "You are an SRE diagnosis agent. Given the alert and evidence, "
        "return ONLY JSON matching the Diagnosis schema.\n"
        f"ALERT: {alert.model_dump()}\n"
        f"EVIDENCE: {json.dumps([e.model_dump() for e in evidence])}"
    )
