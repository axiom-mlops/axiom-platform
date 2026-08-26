"""Demo: drive one HPA memory blind-spot incident through the full loop.

    python -m agents.rca.run_demo

Runs against captured platform fixtures by default so it works anywhere.
Point ReadTools/ActTools at the live cluster to run it for real.
"""
from agents.rca.contracts import Alert
from agents.rca.loop import run_incident, write_runbook


def main() -> None:
    alert = Alert(
        fingerprint="a7f3c1",
        alertname="ContainerMemoryNearLimit",
        severity="page",
        service="recommendationservice",
        namespace="boutique",
        summary="Working set >90% of limit for 10m under 2K VU load",
        firing_since="2026-08-24T19:02:00Z",
    )
    print(f"[ALERT]    {alert.alertname} — {alert.namespace}/{alert.service} ({alert.severity})")

    record = run_incident(alert)

    print(f"[OBSERVE]  {len(record.diagnosis.evidence)} pieces of evidence gathered")
    for e in record.diagnosis.evidence:
        print(f"           - {e.source}: {e.finding}")
    print(f"[DIAGNOSE] confidence={record.diagnosis.confidence}")
    print(f"           {record.diagnosis.root_cause}")
    print(f"[PROPOSE]  {record.patch.action} on {record.patch.target} {record.patch.params} "
          f"(risk={record.patch.risk})")
    print(f"[GATE]     approved={record.gate.approved} by {record.gate.approver}")
    if record.verification:
        print(f"[EXECUTE]  patch applied")
        print(f"[VERIFY]   passed={record.verification.passed}")
        for c in record.verification.checks:
            print(f"           - {c}")

    runbook = write_runbook(record)
    path = "docs/runbooks/agent-generated-hpa-memory-blindspot.md"
    with open(path, "w") as f:
        f.write(runbook + "\n")
    print(f"[RUNBOOK]  written to {path}")


if __name__ == "__main__":
    main()
