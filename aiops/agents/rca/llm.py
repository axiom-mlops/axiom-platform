"""LLM backends for the RCA agent.

Design decision: the loop depends on an LLMBackend *interface*, never on a
concrete model. Two implementations ship here.

  DeterministicBackend - no model at all. Canned, correct RCA for a known
    alert class. It is two things at once: the offline test double that lets
    the loop run green with nothing installed, AND the deterministic bypass
    path for known signatures. The cheapest, safest inference is the one you
    never run, so known events resolve in plain code and never reach a model.

  OllamaBackend - the real model (qwen3.5:9b) reached over the SAME
    OpenAI-compatible /v1 contract that vLLM will serve in production. Dev on
    the Mac and the fine-tuned prod checkpoint speak one interface, so the
    agent code never changes between them. The only difference is base_url.

Boundary safety: model output is untrusted input. The model reasons over the
evidence WE gathered; it does not invent evidence, and it does not choose
WHERE a patch lands. Its reply is validated into the Pydantic contract at the
boundary. If that validation fails (bad JSON, unknown params key, out-of-bounds
value), we feed the exact error back and retry once. This is a self-correcting
boundary: the same validation that protects the act plane also teaches the
model how to fix its own output.

Domain knowledge, for now, lives in the prompts (the "add a memory target,
never replace CPU" rule). That is the honest interim: it is baked in here
until RAG externalizes it into runbooks and the fine-tune internalizes it.
Saying where the knowledge lives, and where it is going, is the story.
"""
from __future__ import annotations

import json
from typing import Callable, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from .contracts import Alert, Diagnosis, Evidence, ProposedPatch

T = TypeVar("T", bound=BaseModel)


class LLMBackend(Protocol):
    """The only surface the loop knows about. Any backend that satisfies this
    (deterministic, Ollama, vLLM, a fine-tuned checkpoint) drops in unchanged."""

    def diagnose(self, alert: Alert, evidence: list[Evidence]) -> Diagnosis: ...
    def propose(self, alert: Alert, diagnosis: Diagnosis) -> ProposedPatch: ...


# --------------------------------------------------------------------------- #
# Offline baseline / known-signature bypass. No network, no model, no deps.   #
# --------------------------------------------------------------------------- #
class DeterministicBackend:
    """Correct RCA for the HPA memory blind-spot, hard-coded. Runs anywhere."""

    def diagnose(self, alert: Alert, evidence: list[Evidence]) -> Diagnosis:
        return Diagnosis(
            root_cause=(
                f"{alert.service} is memory-bound, but its HorizontalPodAutoscaler "
                "targets CPU only. Under load the memory working set pins against the "
                "container limit while CPU stays low, so the HPA never adds replicas "
                "for the resource that is actually saturated. Pods OOM-kill and "
                "restart. Because HPA v2 scales on the maximum across its metrics, the "
                "fix is to add a memory target alongside CPU, not to swap the metric."
            ),
            confidence="high",
            evidence=evidence,
            blast_radius=(
                f"Requests to {alert.service} in {alert.namespace} see rising latency "
                "and 5xx as pods OOM-restart. Upstream callers retry and amplify load, "
                "so the saturation is self-reinforcing until the binding resource is "
                "addressed."
            ),
            prevention=[
                "Confirm the binding resource (memory vs CPU) before touching replica counts.",
                "Check the HPA targets cover the binding resource, not just CPU.",
                "Horizontal scale beats limit raises for horizontally scalable services.",
            ],
        )

    def propose(self, alert: Alert, diagnosis: Diagnosis) -> ProposedPatch:
        return ProposedPatch(
            action="patch_hpa_add_memory_target",
            target=alert.service,
            namespace=alert.namespace,
            # Additive and non-destructive: supply only the NEW memory target.
            # The existing CPU target is read from the live HPA and preserved.
            params={"memory_target_average_utilization": 70},
            rationale=(
                "Add a memory utilization target to the HPA so the binding resource "
                "drives scaling, preserving the existing CPU target (read from the "
                "live spec) so CPU-bound spikes still scale. HPA v2 uses the max "
                "desired replicas across metrics, so both targets coexist safely. "
                "Horizontal scale is preferred over raising limits for a "
                "horizontally scalable service."
            ),
            risk="low",
        )


# --------------------------------------------------------------------------- #
# Real model over the OpenAI-compatible contract (Ollama now, vLLM in prod).   #
# --------------------------------------------------------------------------- #
_DIAGNOSE_SYSTEM = (
    "You are an SRE incident diagnostician. You receive a firing alert and a "
    "fixed sweep of evidence (metrics, HPA spec, logs). Reason about the ROOT "
    "cause, not the symptom. Explicitly distinguish the binding resource (what "
    "is actually saturated) from what the autoscaler is configured to watch. "
    "Kubernetes rule you must apply: HPA v2 computes desired replicas for each "
    "metric independently and scales on the MAXIMUM, so when the autoscaler "
    "watches the wrong resource the fix is to ADD the missing resource target "
    "alongside the existing one, never to replace it. "
    "Respond with JSON only, matching the schema. Ground every claim in the "
    "supplied evidence; do not invent metrics."
)

_PROPOSE_SYSTEM = (
    "You propose exactly ONE remediation, chosen only from the allowed action "
    "set in the schema enum. Respond with JSON only. Prefer the least-risk "
    "action that addresses the diagnosed binding resource.\n"
    "The params object MUST match the chosen action EXACTLY, with no extra keys "
    "and no missing keys:\n"
    "  patch_hpa_add_memory_target -> {\"memory_target_average_utilization\": int "
    "1-100}  (supply ONLY the new memory target; the existing CPU target is "
    "preserved by the system, do NOT include a cpu field)\n"
    "  scale_deployment -> {\"replicas\": int 1-100}\n"
    "  rollback_deployment -> {\"to_revision\": int >=1}  (omit to_revision for the "
    "previous revision)\n"
    "Do not put service, namespace, or target inside params; those are supplied "
    "by the system, not by you."
)

_DIAGNOSE_SCHEMA = {
    "type": "object",
    "properties": {
        "root_cause": {"type": "string"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "blast_radius": {"type": "string"},
    },
    "required": ["root_cause", "confidence", "blast_radius"],
}

_PROPOSE_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": [
                "patch_hpa_add_memory_target",
                "scale_deployment",
                "rollback_deployment",
            ],
        },
        "params": {"type": "object"},
        "rationale": {"type": "string"},
        "risk": {"type": "string", "enum": ["low", "medium", "high"]},
    },
    "required": ["action", "params", "rationale", "risk"],
}


class OllamaBackend:
    """qwen3.5:9b via the local OpenAI-compatible endpoint."""

    def __init__(
        self,
        model: str = "qwen3.5:9b",
        base_url: str = "http://localhost:11434/v1",
        api_key: str = "ollama",
    ):
        from openai import OpenAI  # lazy: the offline path needs no openai install

        self.model = model
        self.client = OpenAI(base_url=base_url, api_key=api_key)

    def _raw(self, system: str, user: str, schema: dict, name: str) -> str:
        """One structured call. Try json_schema; fall back to json_object."""
        def call(sys_msg: str, fmt: dict) -> str:
            resp = self.client.chat.completions.create(
                model=self.model,
                temperature=0,
                messages=[
                    {"role": "system", "content": sys_msg},
                    {"role": "user", "content": user},
                ],
                response_format=fmt,
            )
            return resp.choices[0].message.content

        try:
            return call(system, {"type": "json_schema",
                                 "json_schema": {"name": name, "schema": schema}})
        except Exception:
            return call(system + "\nJSON schema: " + json.dumps(schema),
                        {"type": "json_object"})

    def _ask_and_build(
        self, system: str, user: str, schema: dict, name: str, build: Callable[[dict], T]
    ) -> T:
        """Ask, parse, and validate into a contract. On a JSON error OR a schema
        validation error, feed the exact error back and retry once. If the retry
        still fails, the exception propagates (the loop's low-confidence path and
        the human gate are the backstop)."""
        raw = self._raw(system, user, schema, name)
        try:
            return build(json.loads(raw))
        except (json.JSONDecodeError, ValidationError) as err:
            repair = (
                f"{user}\n\nYour previous reply was rejected:\n{err}\n"
                "Return corrected JSON only, obeying the params rules exactly."
            )
            raw = self._raw(system, repair, schema, name)
            return build(json.loads(raw))

    def diagnose(self, alert: Alert, evidence: list[Evidence]) -> Diagnosis:
        ev = "\n".join(f"- [{e.source}] {e.query} -> {e.finding}" for e in evidence)
        user = (
            f"ALERT: {alert.alertname} on {alert.service} (severity={alert.severity})\n"
            f"SUMMARY: {alert.summary}\n\nEVIDENCE:\n{ev}"
        )
        # Evidence is OUR ground truth, injected here, not restated by the model.
        return self._ask_and_build(
            _DIAGNOSE_SYSTEM, user, _DIAGNOSE_SCHEMA, "diagnosis",
            lambda d: Diagnosis(evidence=evidence, **d),
        )

    def propose(self, alert: Alert, diagnosis: Diagnosis) -> ProposedPatch:
        user = (
            f"SERVICE: {alert.service}\n"
            f"ROOT CAUSE: {diagnosis.root_cause}\n"
            f"BLAST RADIUS: {diagnosis.blast_radius}"
        )
        # The loop pins WHERE (target/namespace) from the validated alert;
        # the model only chooses WHAT among whitelisted actions, with params
        # that must satisfy the per-action contract in contracts.py.
        return self._ask_and_build(
            _PROPOSE_SYSTEM, user, _PROPOSE_SCHEMA, "proposed_patch",
            lambda d: ProposedPatch(target=alert.service, namespace=alert.namespace, **d),
        )


# --------------------------------------------------------------------------- #
# Specialist deterministic backends (agents 2 and 3). Same LLMBackend shape,   #
# same contracts. The real-model path for these is Block B, added behind the   #
# identical interface, exactly as the RCA agent already has.                    #
# --------------------------------------------------------------------------- #
class CanaryDeterministicBackend:
    """Agent 2: canary verification. Decides promote vs roll back from the
    canary's golden signals against baseline."""

    def diagnose(self, alert: Alert, evidence: list[Evidence]) -> Diagnosis:
        return Diagnosis(
            root_cause=(
                f"The canary revision of {alert.service} regresses against baseline: "
                "canary 5xx ratio and p95 latency both exceed the promotion SLO gate. "
                "The regression tracks the new revision, not load, so promoting it "
                "would expose all traffic to the fault."
            ),
            confidence="high",
            evidence=evidence,
            blast_radius=(
                f"Only the canary slice of {alert.service} traffic is affected now. "
                "Promotion would widen the blast radius to 100% of users; the safe "
                "direction is back, not forward."
            ),
            prevention=[
                "Gate promotion on error-rate and latency SLOs, not just a soak timer.",
                "Keep the canary weight low until the analysis run passes.",
                "Ensure automated rollback triggers before a human is paged, not after.",
            ],
        )

    def propose(self, alert: Alert, diagnosis: Diagnosis) -> ProposedPatch:
        return ProposedPatch(
            action="rollback_deployment",
            target=alert.service,
            namespace=alert.namespace,
            params={"to_revision": None},  # None = last known-good revision
            rationale=(
                "The canary failed the SLO gate on error rate and latency. Roll back "
                "to the last known-good revision before promotion widens the blast "
                "radius. Rolling back is reversible and lower risk than shipping a "
                "known-bad release."
            ),
            risk="medium",
        )


class RightSizingDeterministicBackend:
    """Agent 3: right-sizing. Aligns requests to observed usage to cut waste."""

    def diagnose(self, alert: Alert, evidence: list[Evidence]) -> Diagnosis:
        return Diagnosis(
            root_cause=(
                f"{alert.service} reserves far more than it uses: 7-day p95 CPU is a "
                "small fraction of the CPU request and p95 memory sits well under the "
                "memory request, with peak still leaving ample headroom. The requests "
                "were set defensively and never corrected, so the deployment holds "
                "reserved capacity it does not need."
            ),
            confidence="high",
            evidence=evidence,
            blast_radius=(
                "No user-facing impact. The cost is reserved-but-idle capacity that "
                "worsens bin-packing and inflates node count and spend."
            ),
            prevention=[
                "Set requests from observed p95 usage plus headroom, not from guesses.",
                "Review requests-vs-usage on a schedule; usage drifts as traffic changes.",
                "Lower requests with headroom, never straight to raw p95, to absorb spikes.",
            ],
        )

    def propose(self, alert: Alert, diagnosis: Diagnosis) -> ProposedPatch:
        return ProposedPatch(
            action="patch_deployment_resources",
            target=alert.service,
            namespace=alert.namespace,
            # Right-size to observed p95 plus ~30% headroom (millicores / MiB).
            params={"cpu_request_millicores": 150, "memory_request_mib": 256},
            rationale=(
                "Align requests to observed p95 usage plus roughly 30% headroom, "
                "reclaiming idle reserved capacity without risking CPU throttling or "
                "OOM. Requests are lowered deliberately with headroom, not to the raw "
                "p95, so a normal spike is still covered."
            ),
            risk="low",
        )
