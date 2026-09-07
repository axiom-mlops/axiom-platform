"""Live watcher: poll real signals, and when the detector fires, drive the
incident through the existing agent pipeline and print the self-written runbook.
This is the loop you watch in the terminal while k6 drives load.

  python -m rca.watch                 # live: kubectl + Prometheus on localhost
  python -m rca.watch --mock          # no cluster: scripted ramp, see it fire
  python -m rca.watch --once          # single poll then exit (smoke test)

The loop contains no model. It senses, it detects, and only on a FRESH firing
does it hand off to the agents. That hand-off is one call, handle_alert, so
everything already tested (routing, gate, executor, runbook) is reused verbatim.
The watcher's only new job is turning live numbers into an Alert.
"""
from __future__ import annotations

import argparse
import hashlib
import math
import time
from datetime import datetime, timezone

from .contracts import Alert
from .live_reads import (
    Detector,
    DetectionEvent,
    HpaState,
    KubectlReads,
    PromReads,
    Readings,
)
from .loop import handle_alert, write_runbook


def gather(
    prom: PromReads, kube: KubectlReads, svc: str, ns: str, hpa_name: str
) -> Readings:
    """One poll: HPA state from the API server, load fractions from Prometheus."""
    hpa = kube.hpa(hpa_name)
    return Readings(
        service=svc,
        namespace=ns,
        hpa=hpa,
        mem_frac=prom.mem_frac_of_limit(ns, svc),
        cpu_frac=prom.cpu_frac_of_request(ns, svc),
        oom_5m=prom.oom_events_5m(ns, svc),
    )


def _fingerprint(alertname: str, svc: str) -> str:
    return hashlib.sha1(f"{alertname}:{svc}".encode()).hexdigest()[:12]


def to_alert(ev: DetectionEvent) -> Alert:
    """The detector speaks in numbers; the pipeline speaks in Alerts. This is
    the single translation point, and every summary field is real, measured
    data, not a canned string."""
    r = ev.readings
    return Alert(
        fingerprint=_fingerprint("HighMemorySaturation", r.service),
        alertname="HighMemorySaturation",
        severity="page",
        service=r.service,
        namespace=r.namespace,
        summary=(
            f"memory {r.mem_frac:.0%} of limit while HPA pinned at "
            f"{r.hpa.current_replicas}/{r.hpa.max_replicas} and CPU "
            f"{r.cpu_frac:.0%} of request; HPA has no memory target"
        ),
        firing_since=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )


def status_line(ev: DetectionEvent) -> str:
    r = ev.readings
    ts = datetime.now().strftime("%H:%M:%S")
    mem = "  nan" if math.isnan(r.mem_frac) else f"{r.mem_frac:.2f}"
    cpu = "  nan" if math.isnan(r.cpu_frac) else f"{r.cpu_frac:.2f}"
    tag = {
        "ok": "  ok   ",
        "pending": "PENDING",
        "firing": "FIRING ",
        "resolved": "RESOLVD",
    }[ev.kind]
    memflag = "mem-target" if r.hpa.has_memory_metric else "cpu-only  "
    return (
        f"[{ts}] {tag} {r.service:<20} "
        f"hpa={r.hpa.current_replicas}/{r.hpa.max_replicas} {memflag} "
        f"mem={mem}lim cpu={cpu}req oom5m={r.oom_5m:.0f}  {ev.reason}"
    )


def _drive(ev: DetectionEvent, registry=None) -> None:
    """A fresh firing: route to the specialist and run the pipeline."""
    alert = to_alert(ev)
    agent, record = handle_alert(alert, registry=registry)
    if agent is None:
        print("    -> no specialist matched; escalating to human\n")
        return
    print(f"    -> routed to {agent.name}")
    print(
        f"    -> proposed {record.patch.action} "
        f"params={record.patch.params} risk={record.patch.risk}"
    )
    print(f"    -> gate approved={record.gate.approved} by {record.gate.approver}")
    if record.verification:
        print(f"    -> verified passed={record.verification.passed}")
    print()
    print(write_runbook(record))
    print()


def watch(
    svc: str,
    ns: str,
    hpa_name: str,
    prom_url: str,
    interval: float,
    for_polls: int,
    once: bool,
    registry=None,
) -> None:
    prom = PromReads(prom_url)
    kube = KubectlReads(ns)
    detector = Detector(for_polls=for_polls)
    print(f"=== watching {ns}/{svc} (hpa {hpa_name}) every {interval}s ===")
    print(f"    prometheus={prom_url}  fire-after={for_polls} consecutive polls\n")
    while True:
        try:
            r = gather(prom, kube, svc, ns, hpa_name)
            ev = detector.evaluate(r)
        except Exception as e:  # a read failure is not an incident, do not fire
            print(f"[{datetime.now():%H:%M:%S}]  read-error: {e}")
            if once:
                return
            time.sleep(interval)
            continue
        print(status_line(ev))
        if ev.kind == "firing":
            _drive(ev, registry)
        if once:
            return
        time.sleep(interval)


# --- mock mode: see the state machine fire without a cluster ---------------- #
def _mock_readings(svc: str, ns: str):
    hpa = HpaState(
        name=f"{svc}-hpa",
        current_replicas=8,
        desired_replicas=8,
        min_replicas=2,
        max_replicas=8,
        metrics=("cpu",),  # the blind spot: CPU only, no memory target
        at_max=True,
    )
    # memory ramps over the limit while cpu stays low, then recovers after fix
    ramp = [0.40, 0.55, 0.72, 0.91, 0.93, 0.94, 0.61, 0.42]
    for mem in ramp:
        yield Readings(
            service=svc,
            namespace=ns,
            hpa=hpa,
            mem_frac=mem,
            cpu_frac=0.22,
            oom_5m=(3 if mem >= 0.90 else 0),
        )


def watch_mock(svc: str, ns: str, for_polls: int) -> None:
    detector = Detector(for_polls=for_polls)
    print(f"=== MOCK watch {ns}/{svc}: scripted memory ramp, no cluster ===")
    print(f"    fire-after={for_polls} consecutive polls\n")
    for r in _mock_readings(svc, ns):
        ev = detector.evaluate(r)
        print(status_line(ev))
        if ev.kind == "firing":
            _drive(ev, None)
        time.sleep(0.6)


def main() -> None:
    p = argparse.ArgumentParser(description="Live HPA saturation watcher")
    p.add_argument("--service", default="cartservice")
    p.add_argument("--namespace", default="boutique")
    p.add_argument("--hpa", default=None, help="HPA name (default: <service>)")
    p.add_argument("--prom", default="http://localhost:9090")
    p.add_argument("--interval", type=float, default=5.0)
    p.add_argument("--for-polls", type=int, default=3)
    p.add_argument("--once", action="store_true")
    p.add_argument("--mock", action="store_true")
    a = p.parse_args()
    hpa_name = a.hpa or a.service
    if a.mock:
        watch_mock(a.service, a.namespace, a.for_polls)
    else:
        watch(
            a.service, a.namespace, hpa_name, a.prom, a.interval, a.for_polls, a.once
        )


if __name__ == "__main__":
    main()
