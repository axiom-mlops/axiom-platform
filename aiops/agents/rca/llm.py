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
        )


# --------------------------------------------------------------------------- #
# Real model over the OpenAI-compatible contract (Ollama now, vLLM in prod).   #
#                                                                             #
# One backend, three specialists. Everything that differs between specialists #
# lives in a ModelTask (the diagnosis framing, the proposal framing, and the  #
# action enum the model may choose from). The call/parse/validate/retry       #
# machinery below does NOT change per specialist. Narrowing the enum per       #
# specialist is defense in depth: the canary agent's schema contains only     #
# rollback_deployment, so even a confused model cannot emit another domain's  #
# action from the canary seat. Routing chooses the specialist; the schema     #
# bounds what that specialist can even propose.                               #
# --------------------------------------------------------------------------- #
from dataclasses import dataclass


@dataclass(frozen=True)
class ModelTask:
    """Per-specialist configuration for the real-model backend."""
    name: str
    diagnose_system: str
    propose_system: str
    allowed_actions: tuple[str, ...]


_DIAGNOSE_SCHEMA = {
    "type": "object",
    "properties": {
        "root_cause": {"type": "string"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "blast_radius": {"type": "string"},
    },
    "required": ["root_cause", "confidence", "blast_radius"],
}


def _propose_schema(allowed_actions: tuple[str, ...]) -> dict:
    """Build the proposal schema for exactly the actions this specialist owns.
    The enum is the whitelist; anything outside it is unrepresentable. Note there
    is no `risk` field: risk is system-assigned by action (RISK_BY_ACTION in
    contracts.py), not something the model gets to choose."""
    return {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": list(allowed_actions)},
            "params": {"type": "object"},
            "rationale": {"type": "string"},
        },
        "required": ["action", "params", "rationale"],
    }


# --- Agent 1: RCA / autoscaling saturation ---------------------------------- #
_RCA_DIAGNOSE = (
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

_RCA_PROPOSE = (
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

RCA_TASK = ModelTask(
    name="rca-autoscaling",
    diagnose_system=_RCA_DIAGNOSE,
    propose_system=_RCA_PROPOSE,
    # This specialist has real latitude: add a memory target, or scale, or roll
    # back. The menu is small and every item is safe; the diagnosis chooses.
    allowed_actions=("patch_hpa_add_memory_target", "scale_deployment",
                     "rollback_deployment"),
)


# --- Agent 2: canary verification ------------------------------------------- #
_CANARY_DIAGNOSE = (
    "You are an SRE analyzing a canary release against its baseline. You receive "
    "a firing alert and a fixed sweep of evidence: canary-vs-baseline error "
    "ratio, canary-vs-baseline p95 latency, rollout status, and canary logs. "
    "Decide whether the canary is healthy enough to promote or is regressing. A "
    "regression that tracks the new REVISION rather than load means promotion "
    "would widen the blast radius from the canary slice to all traffic. "
    "Respond with JSON only, matching the schema. Ground every claim in the "
    "supplied evidence; do not invent metrics."
)

_CANARY_PROPOSE = (
    "You propose exactly ONE remediation. The ONLY action available to you is "
    "rollback_deployment; you may propose nothing else. Respond with JSON only.\n"
    "params for rollback_deployment: {\"to_revision\": int >= 1}. OMIT to_revision "
    "(or set it to null) to roll back to the last known-good revision. Only set a "
    "specific number if the evidence EXPLICITLY names the target revision. Do NOT "
    "invent a revision number.\n"
    "Do not put service, namespace, or target inside params; those are supplied "
    "by the system, not by you."
)

CANARY_TASK = ModelTask(
    name="canary-verification",
    diagnose_system=_CANARY_DIAGNOSE,
    propose_system=_CANARY_PROPOSE,
    allowed_actions=("rollback_deployment",),
)


# --- Agent 3: right-sizing -------------------------------------------------- #
_RIGHTSIZE_DIAGNOSE = (
    "You are an SRE right-sizing a workload. You receive a firing alert and a "
    "fixed sweep of evidence: 7-day p95 CPU vs request, 7-day p95 memory vs "
    "request, current requests and limits, and 7-day peak memory. Diagnose "
    "whether the workload reserves materially more than it uses, and by how "
    "much. Respond with JSON only, matching the schema. Ground every claim in "
    "the supplied evidence; do not invent metrics."
)

_RIGHTSIZE_PROPOSE = (
    "You propose exactly ONE remediation. The ONLY action available to you is "
    "patch_deployment_resources; you may propose nothing else. Respond with JSON "
    "only.\n"
    "params for patch_deployment_resources: {\"cpu_request_millicores\": int, "
    "\"memory_request_mib\": int, optional \"cpu_limit_millicores\": int, optional "
    "\"memory_limit_mib\": int}.\n"
    "Set requests to the observed p95 usage PLUS roughly 30% headroom, NEVER to "
    "the raw p95, so a normal spike is still covered. Never set a request below a "
    "level that the 7-day PEAK would breach. Units: CPU in millicores (500m = "
    "500), memory in MiB (512Mi = 512).\n"
    "Do not put service, namespace, or target inside params; those are supplied "
    "by the system, not by you."
)

RIGHTSIZE_TASK = ModelTask(
    name="right-sizing",
    diagnose_system=_RIGHTSIZE_DIAGNOSE,
    propose_system=_RIGHTSIZE_PROPOSE,
    allowed_actions=("patch_deployment_resources",),
)


class ModelEmptyResponse(Exception):
    """The model returned an empty/whitespace body. For a reasoning model this
    usually means the token budget was spent on the internal 'reasoning' channel
    before any 'content' was emitted. Treated as a failed parse so the retry (or
    a clean escalation) takes over, never passed downstream as a valid answer."""


class ModelResponseError(Exception):
    """The model failed to produce a valid, schema-conforming answer even after
    one repair attempt. Raised so the caller escalates to a human, rather than
    letting a raw JSONDecodeError propagate. A self-correcting boundary must also
    be fail-SAFE: when the correction fails, it fails cleanly and legibly."""


class OllamaBackend:
    """qwen3.5:9b via the local OpenAI-compatible endpoint. One class, many
    specialists: the ModelTask supplies the per-specialist framing and action
    whitelist; the machinery is shared and identical."""

    def __init__(
        self,
        task: ModelTask = RCA_TASK,
        model: str = "qwen3.5:9b",
        base_url: str = "http://localhost:11434/v1",
        api_key: str = "ollama",
        max_tokens: int = 3000,
        timeout: float = 90.0,
    ):
        from openai import OpenAI  # lazy: the offline path needs no openai install

        self.task = task
        self.model = model
        # Three backstops, learned the hard way from the server log:
        #   reasoning    qwen3.5 is a REASONING model. Left on, it spends the
        #     token budget on an internal monologue and leaves 'content' empty.
        #     The `/no_think` directive in _raw suppresses it at the template
        #     level (the API think:false flag is ignored on this build).
        #   max_tokens   caps OUTPUT. The first cut was 800, which starved even
        #     the answer on a reasoning model; 3000 fits reasoning-plus-JSON yet
        #     is ~7x below the 22k-token runaway that pinned the GPU for 9 min.
        #   timeout      caps WALL CLOCK, the ultimate backstop: a stall or a
        #     full-cap generation fails fast instead of holding the GPU.
        self.max_tokens = max_tokens
        self.client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout)

    def _raw(self, system: str, user: str, schema: dict, name: str) -> str:
        """One structured call over the /v1 contract. We request a json_object
        and put the schema in the system prompt: on this Ollama build json_object
        is honored reliably, whereas the strict json_schema response_format came
        back with empty content.

        Reasoning control: qwen3.5 is a reasoning model, and on this build the
        OpenAI-style `think:false` body flag is SILENTLY IGNORED (proven: a call
        with it set still produced 12k chars of reasoning and hit the token cap
        with empty content). The lever this build DOES honor is the `/no_think`
        directive in the prompt itself, which suppresses reasoning at the chat-
        template level. With it, the same call returns correct content in ~12
        tokens. So we prepend `/no_think` rather than trust the flag.

        An empty/whitespace body is raised as ModelEmptyResponse so it is handled
        as a failed parse, never returned as a valid answer."""
        sys_msg = (
            "/no_think\n" + system +
            "\nRespond with JSON only, matching this schema "
            "(no prose, no markdown fences): " + json.dumps(schema)
        )
        resp = self.client.chat.completions.create(
            model=self.model,
            temperature=0,
            max_tokens=self.max_tokens,
            messages=[
                {"role": "system", "content": sys_msg},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
        )
        content = (resp.choices[0].message.content or "").strip()
        if not content:
            finish = resp.choices[0].finish_reason
            raise ModelEmptyResponse(
                f"empty content for {name} (finish_reason={finish}); "
                "reasoning likely consumed the token budget"
            )
        return content

    def _ask_and_build(
        self, system: str, user: str, schema: dict, name: str, build: Callable[[dict], T]
    ) -> T:
        """Ask, parse, and validate into a contract. On an empty body, bad JSON,
        or a schema validation error, feed the exact problem back and retry ONCE.
        If the retry also fails, raise a clean ModelResponseError so the caller
        escalates to a human, rather than letting a raw decode error propagate.
        A self-correcting boundary must also be fail-safe: when the correction
        fails, it fails cleanly."""
        recoverable = (ModelEmptyResponse, json.JSONDecodeError, ValidationError)

        def attempt(u: str) -> T:
            raw = self._raw(system, u, schema, name)   # may raise ModelEmptyResponse
            return build(json.loads(raw))              # may raise JSONDecodeError/ValidationError

        try:
            return attempt(user)
        except recoverable as err:
            repair = (
                f"{user}\n\nYour previous reply was rejected:\n{err}\n"
                "Return corrected JSON only, obeying the rules exactly. No prose."
            )
            try:
                return attempt(repair)
            except recoverable as err2:
                raise ModelResponseError(
                    f"model failed to produce valid {name} after one repair: {err2}"
                ) from err2

    def diagnose(self, alert: Alert, evidence: list[Evidence]) -> Diagnosis:
        ev = "\n".join(f"- [{e.source}] {e.query} -> {e.finding}" for e in evidence)
        user = (
            f"ALERT: {alert.alertname} on {alert.service} (severity={alert.severity})\n"
            f"SUMMARY: {alert.summary}\n\nEVIDENCE:\n{ev}"
        )
        # Evidence is OUR ground truth, injected here, not restated by the model.
        return self._ask_and_build(
            self.task.diagnose_system, user, _DIAGNOSE_SCHEMA, "diagnosis",
            lambda d: Diagnosis(evidence=evidence, **d),
        )

    def propose(self, alert: Alert, diagnosis: Diagnosis) -> ProposedPatch:
        user = (
            f"SERVICE: {alert.service}\n"
            f"ROOT CAUSE: {diagnosis.root_cause}\n"
            f"BLAST RADIUS: {diagnosis.blast_radius}"
        )
        # The loop pins WHERE (target/namespace) from the validated alert; the
        # model only chooses WHAT among THIS specialist's whitelisted actions,
        # with params that must satisfy the per-action contract in contracts.py.
        return self._ask_and_build(
            self.task.propose_system, user, _propose_schema(self.task.allowed_actions),
            "proposed_patch",
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
        )
