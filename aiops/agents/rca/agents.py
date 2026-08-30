"""The specialist layer: three agents, one shared chassis.

Two orthogonal axes of "multi-agent" live in this package, and keeping them
straight is the whole design story:

  Roles (roles.py)      the INTERNAL decomposition of one incident's lifecycle
                        into typed role-agents: Diagnostician -> Planner ->
                        Executor, with contract-validated handoffs and a
                        model-free Executor. This is depth: how one remediation
                        is made safe and auditable.

  Specialists (here)    the OUTER structure: three agents that each handle a
                        different CLASS of problem, sharing the same chassis
                        (observe -> diagnose -> propose -> gate -> execute ->
                        verify in loop.run_incident). This is breadth: what the
                        platform can do.

An Agent here is a thin specialization of the chassis: which alerts it handles
(routing), the fixed evidence sweep for its class (plan), and the reasoning
backend that turns evidence into a Diagnosis and a whitelisted ProposedPatch.
The loop's safety properties are unchanged; only the plan and backend vary.

Roster, each mapped to a whitelisted action the contract already carries:
  rca-autoscaling      multi-signal saturation, HPA blind spot   -> patch_hpa_add_memory_target
  canary-verification  bad release caught at the canary          -> rollback_deployment
  right-sizing         reserved capacity that outstrips real use  -> patch_deployment_resources
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .contracts import Alert
from .llm import (
    CanaryDeterministicBackend,
    DeterministicBackend,
    LLMBackend,
    RightSizingDeterministicBackend,
)

# Fixed evidence sweeps, one per specialist. Deterministic plans (not
# LLM-chosen) so each alert class is investigated the same way every time:
# testable and rate-limit-safe. {svc}/{ns} are filled from the validated alert.
RCA_PLAN = [
    ("prometheus", "container_memory_working_set_bytes{{pod=~'{svc}.*'}} / limit"),
    ("prometheus", "rate(container_cpu_usage_seconds_total{{pod=~'{svc}.*'}}[5m])"),
    ("kubernetes", "hpa/{svc} spec"),
    ("loki", '{{namespace="{ns}", pod=~"{svc}.*"}} |= "error"'),
]

CANARY_PLAN = [
    ("prometheus",
     "sum(rate(http_requests_total{{svc='{svc}',track='canary',code=~'5..'}}[5m])) "
     "/ sum(rate(http_requests_total{{svc='{svc}',track='canary'}}[5m]))"),
    ("prometheus",
     "histogram_quantile(0.95, sum by (le)("
     "rate(http_request_duration_seconds_bucket{{svc='{svc}',track='canary'}}[5m])))"),
    ("kubernetes", "rollout/{svc} status"),
    ("loki", '{{namespace="{ns}", pod=~"{svc}.*", track="canary"}} |= "error"'),
]

RIGHTSIZE_PLAN = [
    ("prometheus",
     "quantile_over_time(0.95, "
     "rate(container_cpu_usage_seconds_total{{pod=~'{svc}.*'}}[5m])[7d:5m]) / cpu_request"),
    ("prometheus",
     "quantile_over_time(0.95, "
     "container_memory_working_set_bytes{{pod=~'{svc}.*'}}[7d:5m]) / memory_request"),
    ("kubernetes", "deployment/{svc} resources"),
    ("prometheus", "max_over_time(container_memory_working_set_bytes{{pod=~'{svc}.*'}}[7d])"),
]


@dataclass
class Agent:
    name: str
    description: str
    plan: list[tuple[str, str]]
    backend: LLMBackend
    handles: Callable[[Alert], bool]


def _is_saturation(a: Alert) -> bool:
    n = a.alertname.lower()
    return any(k in n for k in ("memory", "saturation", "oom"))


def _is_canary(a: Alert) -> bool:
    n = a.alertname.lower()
    return any(k in n for k in ("canary", "rollout", "release"))


def _is_rightsizing(a: Alert) -> bool:
    n = a.alertname.lower()
    return any(k in n for k in ("overprovision", "underutil", "rightsize", "waste", "idle"))


def build_registry(use_model: bool = False) -> list[Agent]:
    """The three specialists. use_model swaps the RCA agent onto the real model
    (qwen3.5:9b) behind the same interface; agents 2 and 3 gain their real-model
    path in Block B. Everything else is identical between modes."""
    if use_model:
        from .llm import OllamaBackend
        rca_backend: LLMBackend = OllamaBackend()
    else:
        rca_backend = DeterministicBackend()

    return [
        Agent(
            name="rca-autoscaling",
            description="Diagnoses multi-signal saturation and fixes autoscaling blind spots.",
            plan=RCA_PLAN,
            backend=rca_backend,
            handles=_is_saturation,
        ),
        Agent(
            name="canary-verification",
            description="Analyzes a canary against baseline and rolls back a bad release.",
            plan=CANARY_PLAN,
            backend=CanaryDeterministicBackend(),
            handles=_is_canary,
        ),
        Agent(
            name="right-sizing",
            description="Aligns requests to real usage to reclaim idle reserved capacity.",
            plan=RIGHTSIZE_PLAN,
            backend=RightSizingDeterministicBackend(),
            handles=_is_rightsizing,
        ),
    ]


def route(alert: Alert, registry: list[Agent]) -> Agent | None:
    """First specialist whose predicate matches. None means no specialist owns
    this alert, which the orchestrator treats as escalate-to-human rather than
    guess. Order matters only if predicates overlap; keep them disjoint."""
    for agent in registry:
        if agent.handles(alert):
            return agent
    return None


def default_agent() -> Agent:
    """Backward-compatible default so run_incident(alert) with no agent still
    behaves as the deterministic RCA agent (keeps roles.py tests green)."""
    return build_registry(use_model=False)[0]
