"""End-to-end demo of the three-specialist platform.

Run as a module from the directory that CONTAINS the rca package
(i.e. sre/aiops/agents):

  python -m rca.demo_run            # three incidents, deterministic backends
  python -m rca.demo_run ollama     # RCA agent on real qwen3.5:9b, others deterministic

Each incident is routed to the specialist that owns its alert class, then driven
through the shared chassis (Diagnostician -> Planner -> gate -> Executor).
"""
from __future__ import annotations

import sys

from .agents import build_registry
from .contracts import Alert
from .loop import handle_alert, write_runbook


def sample_alerts() -> list[Alert]:
    return [
        Alert(
            fingerprint="a1", alertname="HighMemorySaturation", severity="page",
            service="cartservice", namespace="boutique",
            summary="memory working set >90% of limit for 10m while HPA at max replicas, CPU low",
            firing_since="2026-08-31T15:04:00Z",
        ),
        Alert(
            fingerprint="b2", alertname="CanaryErrorRateHigh", severity="page",
            service="checkoutservice", namespace="boutique",
            summary="canary revision 5xx ratio and p95 latency over the promotion SLO gate",
            firing_since="2026-08-31T15:20:00Z",
        ),
        Alert(
            fingerprint="c3", alertname="DeploymentOverprovisioned", severity="ticket",
            service="productcatalogservice", namespace="boutique",
            summary="7-day p95 CPU and memory far below requests; reserved capacity idle",
            firing_since="2026-08-31T09:00:00Z",
        ),
    ]


def main() -> None:
    use_model = len(sys.argv) > 1 and sys.argv[1] == "ollama"
    registry = build_registry(use_model=use_model)
    mode = "ollama (RCA on qwen3.5:9b)" if use_model else "deterministic"
    print(f"=== Agentic AIOps platform: 3 specialists, {mode} ===")
    print(f"Registered agents: {', '.join(a.name for a in registry)}\n")

    for alert in sample_alerts():
        agent, record = handle_alert(alert, registry=registry)
        print("=" * 78)
        if agent is None:
            print(f"[{alert.alertname}] no specialist matched -> escalate to human")
            continue
        print(f"[ALERT]  {alert.alertname} on {alert.namespace}/{alert.service}")
        print(f"[ROUTE]  -> {agent.name}: {agent.description}")
        print(f"[ACT]    {record.patch.action} params={record.patch.params} "
              f"(risk={record.patch.risk})")
        print(f"[GATE]   approved={record.gate.approved} by {record.gate.approver}")
        if record.verification:
            print(f"[VERIFY] passed={record.verification.passed}")
        print()
        print(write_runbook(record))
        print()


if __name__ == "__main__":
    main()
