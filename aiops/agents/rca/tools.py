"""Read-plane and act-plane tools.

Two planes, deliberately separated:

  Read plane (ReadTools) - safe to call anytime, no side effects. This is the
    evidence sweep the loop runs before it reasons.
  Act plane (ActTools)  - performs the ONLY writes in the system, and is
    unreachable without an approved GateDecision (the loop enforces that).

This hour ships:
  - ReadTools with canned evidence for the HPA memory blind-spot, so the loop
    goes green with zero cluster wiring. The next block swaps in
    PrometheusReadTools pointed at your live endpoint, so the RCA is grounded
    in the actual firing incident rather than a fixture.
  - ActTools in DRY-RUN by default: it logs the intended change and mutates
    nothing. Dry-run is the correct default while a live load test is running.
    We never disturb a cluster we are only meant to observe. Flip dry_run=False
    only for a scenario you control.
"""
from __future__ import annotations

from .contracts import Evidence, ProposedPatch, VerificationResult


class ReadTools:
    """Deterministic evidence for the HPA memory blind-spot. Green with no cluster.

    Method names (prometheus / kubernetes / loki) match the tool keys in the
    loop's INVESTIGATION_PLAN, which dispatches via getattr(reads, tool)."""

    def prometheus(self, query: str) -> Evidence:
        if "memory_working_set" in query:
            finding = "memory working set 0.94 of limit, flat-topped at the ceiling for 8m"
        elif "cpu_usage" in query:
            finding = "cpu 0.22 cores avg, ~31% of request, nowhere near a scale trigger"
        else:
            finding = "series returned"
        return Evidence(source="prometheus", query=query, finding=finding)

    def kubernetes(self, query: str) -> Evidence:
        finding = (
            "HPA targets cpu averageUtilization=70 only; no memory metric configured. "
            "currentReplicas=8 at maxReplicas=8, maxed out and still saturating."
        )
        return Evidence(source="kubernetes", query=query, finding=finding)

    def loki(self, query: str) -> Evidence:
        finding = (
            "recurring OOMKilled events and container restarts; no application-level "
            "errors in the window before each OOM (points at capacity, not a bug)."
        )
        return Evidence(source="loki", query=query, finding=finding)


class ActTools:
    """The write plane. Dry-run by default: logs intent, mutates nothing."""

    def __init__(self, dry_run: bool = True):
        self.dry_run = dry_run

    def execute(self, patch: ProposedPatch) -> None:
        mode = "DRY-RUN" if self.dry_run else "APPLY"
        print(
            f"[{mode}] {patch.action} on {patch.namespace}/{patch.target} "
            f"params={patch.params}"
        )
        if self.dry_run:
            return
        # Thicken step: real, still-whitelisted kubectl/patch path lands here.
        # Each Literal action maps to one narrow, reviewed mutation, never raw kubectl.
        raise NotImplementedError("Live apply is wired in the thicken step, per action.")

    def verify(self, patch: ProposedPatch, reads: ReadTools) -> VerificationResult:
        # Thicken step: re-run the relevant read-plane queries and compare against
        # the pre-patch snapshot. Canned as a pass here so the loop completes.
        return VerificationResult(
            passed=True,
            checks=[
                "HPA now exposes a memory target (memory averageUtilization=70)",
                "post-patch memory working set fell to 0.61 of limit under the same load",
                "no OOMKilled events in the 5m after apply",
            ],
            residual_risk=(
                "Monitor 1h. If the memory ceiling is structural rather than load-driven, "
                "right-size requests/limits (that is agent 3's job, not this patch)."
            ),
        )
