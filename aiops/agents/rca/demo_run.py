"""End-to-end driver for the RCA agent.

Run it as a module from the directory that CONTAINS the rca package
(i.e. sre/aiops/agents), so the relative imports resolve:

  python -m rca.demo_run           -> DeterministicBackend (offline, no model)
  python -m rca.demo_run ollama    -> OllamaBackend, real qwen3.5:9b via /v1

Both drive the identical loop. Only the backend swaps.
"""
from __future__ import annotations

import sys

from .contracts import Alert
from .loop import run_incident, write_runbook


def hpa_blind_spot_alert() -> Alert:
    return Alert(
        fingerprint="a1b2c3d4",
        alertname="HighMemorySaturation",
        severity="page",
        service="cartservice",
        namespace="boutique",
        summary=(
            "cartservice memory working set sustained above 90% of limit for 10m "
            "while the HPA sits at max replicas and CPU stays low"
        ),
        firing_since="2026-08-28T15:04:00Z",
    )


def main() -> None:
    backend = None
    label = "DeterministicBackend (offline)"
    if len(sys.argv) > 1 and sys.argv[1] == "ollama":
        from .llm import OllamaBackend

        backend = OllamaBackend()
        label = "OllamaBackend (qwen3.5:9b via /v1)"

    print(f"=== Running RCA loop with {label} ===\n")
    record = run_incident(hpa_blind_spot_alert(), llm=backend)

    print(write_runbook(record))
    print("\n=== Typed audit record (IncidentRecord) ===")
    print(record.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
